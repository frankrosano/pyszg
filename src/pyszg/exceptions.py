"""pyszg exceptions.

Library callers (notably the szg-hass integration) catch these to
classify failures into Home Assistant error types:

- AuthenticationError       -> ConfigEntryAuthFailed (triggers reauth flow)
- InteractiveLoginRequired  -> config flow falls back to browser login
                               (NOT reauth-failed). Subclass of
                               AuthenticationError, so catch it *before*
                               AuthenticationError.
- SZGConnectionError        -> ConfigEntryNotReady / UpdateFailed
- SZGTimeoutError           -> ConfigEntryNotReady / UpdateFailed
- CommandError              -> entity unavailable / log warning
"""


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
    invalid/revoked cloud OAuth tokens."""


class InteractiveLoginRequired(AuthenticationError):
    """Headless sign-in cannot complete; the browser flow is required.

    Raised by ``SZGCloudAuth.login_with_password`` when the credentials were
    not rejected but the sign-in cannot finish without a browser, e.g. MFA,
    a consent/terms page, or a changed sign-in page. Callers should fall back
    to the browser-paste flow rather than start a reauth.
    """


class CommandError(SZGError):
    """Appliance or cloud rejected the command."""
