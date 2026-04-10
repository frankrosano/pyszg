"""pyszg — Local and cloud API client for Sub-Zero Group connected appliances."""

from .client import SZGClient
from .cloud_auth import SZGCloudAuth, TokenSet
from .cloud_client import SZGCloudClient

try:
    from .cloud_signalr import SZGCloudSignalR
except ImportError:
    SZGCloudSignalR = None  # websockets not installed
from .appliance import (
    Appliance,
    ApplianceType,
    CookMode,
    ModuleGeneration,
    WashCycle,
    WashStatus,
    TEMP_RANGE_FRIDGE,
    TEMP_RANGE_FREEZER,
    TEMP_RANGE_WINE,
    TEMP_RANGE_OVEN,
)
from .exceptions import (
    SZGError,
    SZGConnectionError,
    AuthenticationError,
    CommandError,
)

# Keep ConnectionError as an alias in __all__ for convenience
ConnectionError = SZGConnectionError

__all__ = [
    "SZGClient",
    "SZGCloudAuth",
    "SZGCloudClient",
    "SZGCloudSignalR",
    "TokenSet",
    "Appliance",
    "ApplianceType",
    "CookMode",
    "ModuleGeneration",
    "WashCycle",
    "WashStatus",
    "TEMP_RANGE_FRIDGE",
    "TEMP_RANGE_FREEZER",
    "TEMP_RANGE_WINE",
    "TEMP_RANGE_OVEN",
    "SZGError",
    "SZGConnectionError",
    "AuthenticationError",
    "CommandError",
]
