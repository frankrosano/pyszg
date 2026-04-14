"""OAuth2 + PKCE authentication for the Sub-Zero Group cloud API."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import ssl
import time
import urllib.parse
import urllib.request
import urllib.error
import webbrowser
from dataclasses import dataclass
from typing import Any

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

        # First time — opens browser for login:
        tokens = auth.login()

        # Subsequent calls — refreshes silently:
        tokens = auth.refresh(tokens)

        # Or load/save from file:
        auth.save_tokens(tokens, "tokens.json")
        tokens = auth.load_tokens("tokens.json")
        tokens = auth.ensure_valid(tokens)  # refreshes if expired
    """

    def login(self, timeout: int = 120) -> TokenSet:
        """Perform interactive browser-based login.

        Opens the B2C login page in the user's default browser.
        After login, the browser redirects to a custom scheme URL.
        The user must copy this URL and save it to a file, or the
        auth code can be provided directly.

        For Home Assistant integration, use login_with_code() instead
        after handling the redirect in the HA OAuth flow.
        """
        code_verifier = secrets.token_urlsafe(64)[:128]
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()
        state = secrets.token_urlsafe(32)

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
        auth_url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

        _LOGGER.info("Opening browser for Sub-Zero login")
        webbrowser.open(auth_url)

        print("\nAfter logging in, the browser will redirect to a URL starting with:")
        print(f"  {REDIRECT_URI}?code=...")
        print("\nCopy the FULL URL and save it to: redirect_url.txt")
        input("\nPress Enter once saved...")

        try:
            with open("redirect_url.txt", "r") as f:
                redirect_url = f.read().strip()
        except FileNotFoundError:
            raise AuthenticationError("redirect_url.txt not found")

        # Extract code
        if "?" in redirect_url:
            qs = redirect_url.split("?", 1)[1]
            params = urllib.parse.parse_qs(qs)
        else:
            params = {}

        if "error" in params:
            raise AuthenticationError(params.get("error_description", params["error"])[0])

        if "code" not in params:
            raise AuthenticationError(f"No auth code in redirect URL")

        code = params["code"][0]
        return self.exchange_code(code, code_verifier)

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
