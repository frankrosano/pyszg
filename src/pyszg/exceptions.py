"""pyszg exceptions.

Library callers (notably the szg-hass integration) catch these to
classify failures into Home Assistant error types:

- AuthenticationError       -> ConfigEntryAuthFailed (triggers reauth flow).
                               Token-endpoint rejections carry an optional
                               ``code`` (the Azure AD B2C ``AADB2C...`` error
                               code, or None) so consumers can tell *why*;
                               see B2C_ERR_GRANT_EXPIRED / B2C_ERR_GRANT_REVOKED.
- InteractiveLoginRequired  -> config flow falls back to browser login
                               (NOT reauth-failed). Subclass of
                               AuthenticationError, so catch it *before*
                               AuthenticationError.
- SZGConnectionError        -> ConfigEntryNotReady / UpdateFailed
- SZGTimeoutError           -> ConfigEntryNotReady / UpdateFailed
- CommandError              -> entity unavailable / log warning
"""

from __future__ import annotations

# Azure AD B2C error codes seen in token-endpoint rejections. Exposed as
# ``AuthenticationError.code`` when the response body contains one.
B2C_ERR_GRANT_EXPIRED = "AADB2C90080"  # grant expired (maximum sign-in age reached)
B2C_ERR_GRANT_REVOKED = "AADB2C90129"  # grant revoked (e.g. after a password change)


class SZGError(Exception):
    """Base exception for pyszg."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class SZGConnectionError(SZGError):
    """Failed to connect to the appliance module or cloud endpoint."""


class SZGTimeoutError(SZGError):
    """A request timed out waiting for a response."""


class AuthenticationError(SZGError):
    """True credential rejection: wrong PIN, wrong email/password, or
    invalid/revoked cloud OAuth tokens.

    ``code`` is an optional machine-readable reason. For a token-endpoint
    4xx it is the first ``AADB2C<digits>`` code found in the response body
    (compare against ``B2C_ERR_GRANT_EXPIRED`` / ``B2C_ERR_GRANT_REVOKED``);
    it is ``None`` when the body has no such code or the error did not come
    from the token endpoint.
    """

    def __init__(
        self,
        message: str,
        status: int | None = None,
        code: str | None = None,
    ):
        super().__init__(message, status)
        self.code = code


class InteractiveLoginRequired(AuthenticationError):
    """Headless sign-in cannot complete; the browser flow is required.

    Raised by ``SZGCloudAuth.login_with_password`` when the credentials were
    not rejected but the sign-in cannot finish without a browser, e.g. MFA,
    a consent/terms page, or a changed sign-in page. Callers should fall back
    to the browser-paste flow rather than start a reauth.
    """


class CommandError(SZGError):
    """Appliance or cloud rejected the command."""
