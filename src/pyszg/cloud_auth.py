"""OAuth2 + PKCE authentication for the Sub-Zero Group cloud API."""

from __future__ import annotations

import base64
import hashlib
import http.cookiejar
import json
import logging
import re
import secrets
import socket
import ssl
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable

from .cloud_const import (
    B2C_HOST, CLIENT_ID, REDIRECT_URI, AUTHORIZE_URL, TOKEN_URL, SCOPES,
)
from .exceptions import (
    AuthenticationError,
    InteractiveLoginRequired,
    SZGConnectionError,
    SZGError,
    SZGTimeoutError,
)

_LOGGER = logging.getLogger(__name__)

# Headless sign-in (login_with_password) tunables.
_B2C_TIMEOUT = 15            # seconds, per B2C HTML/XHR request
_MAX_BODY = 400_000          # max bytes read from any B2C response
_TOKEN_ATTEMPTS = 3          # authorization-code exchange attempts
_TOKEN_RETRY_DELAY = 2.0     # seconds between exchange attempts
# The B2C hosted pages are served to browsers; present as one for those steps
# only. The token endpoint keeps the default urllib UA (see _token_request).
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_SAFE_TENANT_PATH = re.compile(r"^/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")

# Refresh this many seconds before the token's actual expiry. Without a
# margin, a request built right at the boundary can be rejected (HTTP 401)
# by the time it reaches B2C/APIM once network latency and host/Azure clock
# skew are accounted for — which would spuriously trigger a reauth flow.
TOKEN_EXPIRY_MARGIN = 60


@dataclass
class TokenSet:
    """Stored OAuth tokens and derived user info."""
    id_token: str = ""
    refresh_token: str = ""
    user_id: str = ""
    email: str = ""
    name: str = ""
    expires_at: float = 0

    @property
    def is_expired(self) -> bool:
        # Treat the token as expired slightly early so it's proactively
        # refreshed before the boundary rather than failing a live request.
        return time.time() >= (self.expires_at - TOKEN_EXPIRY_MARGIN)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id_token": self.id_token,
            "refresh_token": self.refresh_token,
            "user_id": self.user_id,
            "email": self.email,
            "name": self.name,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenSet:
        return cls(
            id_token=data.get("id_token", ""),
            refresh_token=data.get("refresh_token", ""),
            user_id=data.get("user_id", ""),
            email=data.get("email", ""),
            name=data.get("name", ""),
            expires_at=data.get("expires_at", 0),
        )


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    """Decode a JWT payload without verifying the signature (we trust B2C).

    Returns ``{}`` for a structurally invalid token. Raises on a malformed
    payload segment so callers can surface a meaningful auth error.
    """
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    # Pad to a multiple of 4. ``-len % 4`` yields 0 when already aligned,
    # unlike ``4 - len % 4`` which would append a spurious 4 chars.
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def _token_request(params: dict[str, str]) -> dict[str, Any]:
    """Make a token endpoint request.

    Maps transport failures into the SZG exception hierarchy so every
    refresh/exchange path raises only ``SZGError`` subtypes (an HTTP error
    -> ``AuthenticationError``, a timeout -> ``SZGTimeoutError``, any other
    transport failure -> ``SZGConnectionError``). Without this, a network
    blip during a token refresh would surface as a raw ``URLError`` that
    downstream consumers (e.g. the HA coordinator) don't classify.

    An HTTP 5xx (e.g. a transient 504 gateway timeout, whose body is an HTML
    page) is a service outage, not a credential problem, so it maps to
    ``SZGConnectionError`` (with ``status`` set and no body in the message).
    Only 4xx responses raise ``AuthenticationError``.
    """
    data = urllib.parse.urlencode(params).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    req = urllib.request.Request(TOKEN_URL, data=data, headers=headers)
    ctx = ssl.create_default_context()
    try:
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            raise SZGConnectionError(
                f"Token endpoint unavailable (HTTP {e.code})",
                status=e.code,
            ) from e
        body = e.read().decode()
        # B2C puts an AADB2C##### code in the OAuth error_description; surface
        # it so callers can distinguish e.g. an expired vs revoked grant.
        match = re.search(r"AADB2C\d+", body)
        raise AuthenticationError(
            f"Token request failed (HTTP {e.code}): {body[:300]}",
            status=e.code,
            code=match.group(0) if match else None,
        ) from e
    except socket.timeout as exc:
        raise SZGTimeoutError("Token request timed out") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, socket.timeout):
            raise SZGTimeoutError("Token request timed out") from exc
        raise SZGConnectionError(
            f"Cannot reach token endpoint: {exc.reason}"
        ) from exc


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Don't follow 3xx; the redirect then surfaces as an ``HTTPError``.

    B2C finishes sign-in with a 302 to a custom-scheme URL carrying the
    authorization code. That URL must be read, never fetched.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _build_opener() -> tuple[urllib.request.OpenerDirector, http.cookiejar.CookieJar]:
    """Opener with a cookie jar and no redirect following."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar), _NoRedirectHandler()
    )
    return opener, jar


def _b2c_request(
    opener: urllib.request.OpenerDirector,
    method: str,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], str]:
    """Perform one B2C page/XHR request; return ``(status, headers, body)``.

    Response headers are returned with lower-cased names. 2xx/3xx/4xx
    statuses are returned as values for the caller to interpret; HTTP 5xx
    and 429 and all transport failures raise ``SZGConnectionError`` /
    ``SZGTimeoutError``. Exception messages never include the URL (it
    carries ``tx``/``csrf``) or the request body.
    """
    hdrs = {"User-Agent": _BROWSER_UA}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        resp = opener.open(req, timeout=_B2C_TIMEOUT)
        status = resp.status
        resp_headers = resp.headers
        body = resp.read(_MAX_BODY).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status = e.code
        resp_headers = e.headers or {}
        body = e.read(_MAX_BODY).decode("utf-8", "replace") if e.fp else ""
    except socket.timeout as exc:
        raise SZGTimeoutError("Sign-in request timed out") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, socket.timeout):
            raise SZGTimeoutError("Sign-in request timed out") from exc
        raise SZGConnectionError(
            f"Cannot reach sign-in service: {exc.reason}"
        ) from exc
    except OSError as exc:
        raise SZGConnectionError(
            f"Cannot reach sign-in service: {type(exc).__name__}"
        ) from exc
    if status >= 500 or status == 429:
        raise SZGConnectionError(
            f"Sign-in service unavailable (HTTP {status})", status=status
        )
    return status, {k.lower(): v for k, v in resp_headers.items()}, body


def _pkce_pair() -> tuple[str, str]:
    """Return a PKCE ``(verifier, S256 challenge)`` pair."""
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _parse_settings(page: str) -> dict[str, Any] | None:
    """Extract the inline ``var SETTINGS = {...};`` JSON object, or None."""
    m = re.search(r"var\s+SETTINGS\s*=\s*", page)
    if not m:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(page, m.end())
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _settings_fields(settings: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """Return ``(csrf, trans_id, tenant_path, policy, api)`` from SETTINGS.

    Raises ``InteractiveLoginRequired`` if a key is missing or malformed
    (the sign-in page changed). The path-like values are validated because
    they are interpolated into request URLs.
    """
    hosts = settings.get("hosts")
    hosts = hosts if isinstance(hosts, dict) else {}
    csrf = settings.get("csrf")
    trans_id = settings.get("transId")
    tenant = hosts.get("tenant")
    policy = hosts.get("policy")
    api = settings.get("api")
    if not all(isinstance(v, str) and v for v in (csrf, trans_id, tenant, policy, api)):
        raise InteractiveLoginRequired(
            "Sign-in page has an unexpected layout; use the browser login flow"
        )
    if not (
        _SAFE_TENANT_PATH.match(tenant)
        and _SAFE_SEGMENT.match(policy)
        and _SAFE_SEGMENT.match(api)
    ):
        raise InteractiveLoginRequired(
            "Sign-in page has an unexpected layout; use the browser login flow"
        )
    return csrf, trans_id, tenant, policy, api


def _extract_code(location: str, state: str) -> str:
    """Pull the authorization code out of B2C's final redirect ``Location``.

    B2C appends a trailing ``/`` to the custom-scheme redirect and puts
    ``state`` before ``code``, so compare scheme+host+path (slash tolerant)
    and parse the query rather than matching a literal prefix. The URL is
    never fetched.

    Raises ``InteractiveLoginRequired`` if the redirect is foreign, carries
    an ``error`` or has no code; ``SZGError`` on a ``state`` mismatch.
    """
    split = urllib.parse.urlsplit(location)
    base = f"{split.scheme}://{split.netloc}{split.path}".rstrip("/")
    qs = urllib.parse.parse_qs(split.query)
    code = (qs.get("code") or [""])[0]
    if base != REDIRECT_URI.rstrip("/") or "error" in qs or not code:
        raise InteractiveLoginRequired(
            "Sign-in did not return an authorization code; "
            "use the browser login flow"
        )
    got_state = (qs.get("state") or [""])[0]
    if not secrets.compare_digest(got_state.encode(), state.encode()):
        raise SZGError("State mismatch in sign-in redirect")
    return code


class SZGCloudAuth:
    """Handles OAuth2 authentication with the Sub-Zero Group cloud.

    Usage:
        auth = SZGCloudAuth()

        # Preferred: headless sign-in with the Owner's App email + password
        # (no browser). Raises InteractiveLoginRequired if B2C needs more
        # than a password (e.g. MFA) -- fall back to the browser flow below.
        tokens = auth.login_with_password(email, password)

        # Fallback: construct the authorize URL and have the user log in via
        # browser (see examples/cloud_login.py for the full interactive flow):
        auth_url = auth.get_authorize_url(challenge, state)

        # Exchange the redirect code for tokens:
        tokens = auth.exchange_code(code, verifier)

        # Subsequent calls — refresh silently:
        tokens = auth.refresh(tokens)

        # Load/save from file:
        auth.save_tokens(tokens, "tokens.json")
        tokens = auth.load_tokens("tokens.json")
        tokens = auth.ensure_valid(tokens)  # refreshes if expired
    """

    def exchange_code(self, code: str, code_verifier: str) -> TokenSet:
        """Exchange an authorization code for tokens."""
        resp = _token_request({
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
        })
        return self._parse_token_response(resp)

    def login_with_password(self, email: str, password: str) -> TokenSet:
        """Sign in headlessly with the Owner's App email + password.

        Drives the Azure AD B2C hosted sign-in page with plain HTTP (no
        browser): authorize -> parse ``SETTINGS`` -> POST credentials to
        ``SelfAsserted`` -> read the 302 from ``confirmed`` -> exchange the
        code. The password is used once and never stored, logged or put in
        an exception message.

        Raises:
            AuthenticationError: email/password empty or rejected. Do not
                retry blindly (B2C may lock the account).
            InteractiveLoginRequired: the credentials were not rejected but
                sign-in cannot finish headlessly (MFA, consent page, changed
                page layout). Use the browser flow instead.
            SZGConnectionError / SZGTimeoutError: network failure, HTTP
                5xx/429 from B2C, or the token endpoint stayed unavailable
                after retries. Safe to retry later.
            SZGError: ``state`` mismatch in the final redirect.
        """
        if not email or not email.strip() or not password:
            raise AuthenticationError("Email and password are required")

        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(32)
        auth_url = self.get_authorize_url(challenge, state)
        opener, _jar = _build_opener()

        # Step 1: sign-in page.
        _LOGGER.debug("Headless login: GET authorize")
        status, _hdrs, page = _b2c_request(opener, "GET", auth_url)
        if status != 200:
            raise InteractiveLoginRequired(
                f"Sign-in page unavailable (HTTP {status}); "
                "use the browser login flow"
            )

        # Step 2: SETTINGS (csrf, transId, tenant, policy, api).
        _LOGGER.debug("Headless login: parse SETTINGS")
        settings = _parse_settings(page)
        if settings is None:
            raise InteractiveLoginRequired(
                "Sign-in page has an unexpected layout; use the browser login flow"
            )
        csrf, trans_id, tenant, policy, api = _settings_fields(settings)
        tx = urllib.parse.quote(trans_id, safe="=")
        base = f"https://{B2C_HOST}{tenant}"
        select_url = f"{base}/SelfAsserted?tx={tx}&p={policy}"
        confirm_url = (
            f"{base}/api/{api}/confirmed"
            f"?rememberMe=false&csrf_token={urllib.parse.quote(csrf, safe='')}"
            f"&tx={tx}&p={policy}"
        )

        # Step 3: credentials. Field ids are not discoverable from the page,
        # but signInName/password are what the hosted page posts.
        _LOGGER.debug("Headless login: POST credentials")
        body = urllib.parse.urlencode({
            "request_type": "RESPONSE",
            "signInName": email.strip(),
            "password": password,
        }).encode()
        try:
            status, _hdrs, resp_body = _b2c_request(
                opener, "POST", select_url, data=body,
                headers={
                    "X-CSRF-TOKEN": csrf,
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Origin": f"https://{B2C_HOST}",
                    "Referer": auth_url,
                },
            )
        finally:
            del body, password  # don't keep the secret around
        try:
            parsed = json.loads(resp_body)
        except ValueError:
            parsed = None
        json_status = str(parsed.get("status")) if isinstance(parsed, dict) else None
        if status == 400 or json_status == "400":
            raise AuthenticationError("Invalid email or password", status=400)
        if not (status == 200 and json_status == "200"):
            raise InteractiveLoginRequired(
                "Sign-in returned an unexpected response; "
                "use the browser login flow"
            )

        # Step 4: confirmed -> 302 to the custom-scheme redirect (not fetched).
        _LOGGER.debug("Headless login: GET confirmed")
        status, hdrs, _page2 = _b2c_request(
            opener, "GET", confirm_url, headers={"Referer": auth_url}
        )
        if status not in (301, 302, 303, 307) or not hdrs.get("location"):
            # 200 HTML => MFA / consent / terms page; anything else is odd too.
            raise InteractiveLoginRequired(
                "Sign-in needs an additional step (for example MFA); "
                "use the browser login flow"
            )
        code = _extract_code(hdrs["location"], state)

        # Step 5: exchange the code. A transient 504 here was observed live;
        # retrying the same code succeeded. Retry only transport/5xx errors
        # (never 4xx such as invalid_grant).
        _LOGGER.debug("Headless login: exchange code")
        for attempt in range(1, _TOKEN_ATTEMPTS + 1):
            try:
                return self.exchange_code(code, verifier)
            except (SZGConnectionError, SZGTimeoutError):
                if attempt == _TOKEN_ATTEMPTS:
                    raise
                _LOGGER.debug("Headless login: token exchange attempt %d failed", attempt)
                time.sleep(_TOKEN_RETRY_DELAY)
        raise AssertionError("unreachable")  # pragma: no cover

    def refresh(self, tokens: TokenSet) -> TokenSet:
        """Refresh tokens using the refresh token."""
        if not tokens.refresh_token:
            raise AuthenticationError("No refresh token available. Login required.")
        resp = _token_request({
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": tokens.refresh_token,
            "scope": SCOPES,
        })
        return self._parse_token_response(resp)

    def ensure_valid(self, tokens: TokenSet) -> TokenSet:
        """Return valid tokens, refreshing if expired."""
        if tokens.is_expired:
            _LOGGER.info("Token expired, refreshing")
            return self.refresh(tokens)
        return tokens

    @staticmethod
    def get_authorize_url(code_challenge: str, state: str) -> str:
        """Build the authorize URL for external OAuth flows (e.g., Home Assistant).

        Args:
            code_challenge: PKCE S256 challenge.
            state: CSRF state parameter.

        Returns:
            The full authorize URL to redirect the user to.
        """
        params = {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "response_mode": "query",
        }
        return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    def _parse_token_response(self, resp: dict[str, Any]) -> TokenSet:
        """Parse a token endpoint response into a TokenSet."""
        id_token = resp.get("id_token", "")
        if not id_token:
            raise AuthenticationError("No id_token in response")

        try:
            claims = _decode_jwt_claims(id_token)
        except Exception as exc:
            raise AuthenticationError(f"Failed to decode id_token: {exc}") from exc

        # Prefer the token's own `exp` claim (absolute epoch, in the
        # server's clock domain — exactly what B2C/APIM validate against)
        # over the response's relative `id_token_expires_in`. Fall back to
        # the relative field if the claim is missing or unusable.
        exp_claim = claims.get("exp")
        if isinstance(exp_claim, (int, float)) and exp_claim > 0:
            expires_at = float(exp_claim)
        else:
            expires_in = resp.get("id_token_expires_in", 3600)
            if isinstance(expires_in, str):
                try:
                    expires_in = int(expires_in)
                except ValueError:
                    expires_in = 3600
            expires_at = time.time() + expires_in

        return TokenSet(
            id_token=id_token,
            refresh_token=resp.get("refresh_token", ""),
            user_id=claims.get("extension_sitecoreUserId", claims.get("sub", "")),
            email=claims.get("email", ""),
            name=f"{claims.get('given_name', '')} {claims.get('family_name', '')}".strip(),
            expires_at=expires_at,
        )

    @staticmethod
    def save_tokens(tokens: TokenSet, path: str) -> None:
        """Save tokens to a JSON file."""
        with open(path, "w") as f:
            json.dump(tokens.to_dict(), f, indent=2)

    @staticmethod
    def load_tokens(path: str) -> TokenSet:
        """Load tokens from a JSON file."""
        with open(path) as f:
            return TokenSet.from_dict(json.load(f))


class TokenStore:
    """Thread-safe shared holder for an OAuth ``TokenSet``.

    Solves two problems:

    1. **Persistence on rotation.** Azure AD B2C rotates the refresh
       token on every refresh and invalidates the previous one. If the
       rotated tokens aren't written back to durable storage, the next
       process start (HA restart, daemon restart) tries to refresh with
       a stale refresh_token and gets a 401, forcing reauth. The
       ``on_refresh`` callback fires after every successful refresh so
       the caller can persist the new tokens.

    2. **Single source of truth across clients.** A single ``TokenStore``
       can be shared by ``SZGCloudClient`` and ``SZGCloudSignalR`` so
       they observe the same rotation in lockstep. Without this, each
       client holds its own ``TokenSet``; whichever refreshes first
       invalidates the refresh_token the other still has cached, and
       the loser will fail on its next refresh attempt within the same
       process.

    The store is thread-safe — refresh is serialized with an internal
    lock so concurrent callers from executor threads (cloud client and
    SignalR negotiate / open_cloud_async calls) don't race.

    The ``on_refresh`` callback runs while the refresh lock is held, so
    keep it short. If the consumer needs to do async work (e.g.,
    schedule an HA config-entry update on the event loop), it should
    schedule that work and return immediately rather than block.
    """

    def __init__(
        self,
        tokens: TokenSet,
        auth: SZGCloudAuth | None = None,
        on_refresh: Callable[[TokenSet], None] | None = None,
    ) -> None:
        self._tokens = tokens
        self._auth = auth or SZGCloudAuth()
        self._on_refresh = on_refresh
        self._lock = threading.Lock()

    @property
    def tokens(self) -> TokenSet:
        """Current tokens (may be expired — call ``get_valid()`` to refresh)."""
        return self._tokens

    @property
    def auth(self) -> SZGCloudAuth:
        return self._auth

    def get_valid(self) -> TokenSet:
        """Return current tokens, refreshing first if they're expired.

        Thread-safe: at most one refresh happens at a time. After a
        successful refresh, the ``on_refresh`` callback (if set) is
        invoked with the new ``TokenSet`` so the caller can persist
        the rotated refresh_token. Exceptions raised by the callback
        are logged and swallowed: the in-memory tokens remain valid
        for the running process even if persistence failed. The next
        process start may fall back to reauth, but the current process
        keeps working.
        """
        with self._lock:
            if not self._tokens.is_expired:
                return self._tokens
            return self._refresh_locked()

    def force_refresh(self, stale: TokenSet | None = None) -> TokenSet:
        """Refresh the tokens regardless of the current expiry estimate.

        Used as a backstop when a live request is rejected with HTTP 401
        even though the local clock still considers the token valid (clock
        skew, an out-of-band rotation, or a boundary race). Thread-safe and
        serialized with ``get_valid`` under the same lock, so concurrent
        callers share the rotation rather than racing it. If the refresh
        itself fails (e.g. the refresh_token is genuinely dead), the
        underlying ``AuthenticationError`` propagates so the caller can
        drive a real reauth.

        If ``stale`` is provided and the store has *already* rotated past
        it (another thread refreshed while this caller was mid-request),
        the current tokens are returned without issuing a second refresh.
        This collapses a burst of concurrent 401s — e.g. the HA coordinator
        polling every device at once at a token boundary — into a single
        refresh and a single ``on_refresh`` persistence write.
        """
        with self._lock:
            if stale is not None and self._tokens is not stale:
                return self._tokens
            return self._refresh_locked()

    def _refresh_locked(self) -> TokenSet:
        """Perform a refresh + fire the on_refresh hook. Caller holds the lock."""
        _LOGGER.info("Refreshing OAuth tokens")
        new_tokens = self._auth.refresh(self._tokens)
        self._tokens = new_tokens

        if self._on_refresh is not None:
            try:
                self._on_refresh(new_tokens)
            except Exception:
                _LOGGER.exception(
                    "TokenStore on_refresh callback failed; "
                    "rotated tokens may not be persisted"
                )

        return new_tokens
