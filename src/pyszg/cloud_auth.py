"""OAuth2 + PKCE authentication for the Sub-Zero Group cloud API."""

from __future__ import annotations

import base64
import json
import logging
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
from .exceptions import AuthenticationError

_LOGGER = logging.getLogger(__name__)


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
        return time.time() >= self.expires_at

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
    """Decode JWT payload without verification (we trust B2C)."""
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def _token_request(params: dict[str, str]) -> dict[str, Any]:
    """Make a token endpoint request."""
    data = urllib.parse.urlencode(params).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    req = urllib.request.Request(TOKEN_URL, data=data, headers=headers)
    ctx = ssl.create_default_context()
    try:
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise AuthenticationError(f"Token request failed (HTTP {e.code}): {body[:300]}")


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

        claims = _decode_jwt_claims(id_token)
        expires_in = resp.get("id_token_expires_in", 3600)
        if isinstance(expires_in, str):
            try:
                expires_in = int(expires_in)
            except ValueError:
                expires_in = 3600

        return TokenSet(
            id_token=id_token,
            refresh_token=resp.get("refresh_token", ""),
            user_id=claims.get("extension_sitecoreUserId", claims.get("sub", "")),
            email=claims.get("email", ""),
            name=f"{claims.get('given_name', '')} {claims.get('family_name', '')}".strip(),
            expires_at=time.time() + expires_in,
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

            _LOGGER.info("Token expired, refreshing")
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
