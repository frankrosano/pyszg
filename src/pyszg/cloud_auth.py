"""OAuth2 + PKCE authentication for the Sub-Zero Group cloud API."""

from __future__ import annotations

import base64
import json
import logging
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
    CLIENT_ID, REDIRECT_URI, AUTHORIZE_URL, TOKEN_URL, SCOPES,
)
from .exceptions import (
    AuthenticationError,
    SZGConnectionError,
    SZGTimeoutError,
)

_LOGGER = logging.getLogger(__name__)

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
    """
    data = urllib.parse.urlencode(params).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    req = urllib.request.Request(TOKEN_URL, data=data, headers=headers)
    ctx = ssl.create_default_context()
    try:
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise AuthenticationError(
            f"Token request failed (HTTP {e.code}): {body[:300]}",
            status=e.code,
        ) from e
    except socket.timeout as exc:
        raise SZGTimeoutError("Token request timed out") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, socket.timeout):
            raise SZGTimeoutError("Token request timed out") from exc
        raise SZGConnectionError(
            f"Cannot reach token endpoint: {exc.reason}"
        ) from exc


class SZGCloudAuth:
    """Handles OAuth2 authentication with the Sub-Zero Group cloud.

    Usage:
        auth = SZGCloudAuth()

        # Construct the authorize URL and have the user log in via browser
        # (see examples/cloud_login.py for the full interactive flow):
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
