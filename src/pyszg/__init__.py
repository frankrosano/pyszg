"""pyszg — Local and cloud API client for Sub-Zero Group connected appliances."""

from .client import SZGClient
from .cloud_auth import SZGCloudAuth, TokenSet, TokenStore
from .cloud_client import SZGCloudClient
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
    SZGTimeoutError,
    AuthenticationError,
    CommandError,
)

# SignalR is optional — it needs the `websockets` dependency. Expose it as
# None when that isn't installed so consumers can feature-detect.
try:
    from .cloud_signalr import SZGCloudSignalR
except ImportError:
    SZGCloudSignalR = None

__all__ = [
    "SZGClient",
    "SZGCloudAuth",
    "SZGCloudClient",
    "SZGCloudSignalR",
    "TokenSet",
    "TokenStore",
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
    "SZGTimeoutError",
    "AuthenticationError",
    "CommandError",
]
