"""Appliance model — parsed state from the CAT module response."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


# Temperature ranges (°F) from Control4 driver specifications.
# Some appliances may not support the full range.
TEMP_RANGE_FRIDGE = (34, 45)
TEMP_RANGE_FREEZER = (-5, 5)
TEMP_RANGE_WINE = (40, 65)
TEMP_RANGE_OVEN = (85, 550)


class CookMode(IntEnum):
    """Oven cook mode values. Not all modes available on all models."""
    OFF = 0
    BAKE = 1
    ROAST = 2
    BROIL = 3
    BAKE_STONE = 4
    CONVECTION_BAKE = 5
    CONVECTION_ROAST = 6
    CONVECTION_BROIL = 7
    CONVECTION = 8
    PROOF = 9
    DEHYDRATE = 10
    SELF_CLEAN = 11
    WARM = 12
    ECO = 13
    UNKNOWN = 14


class WashCycle(IntEnum):
    """Dishwasher wash cycle values. Not all cycles available on all models."""
    NONE = 0
    AUTO = 1
    NORMAL = 2
    HEAVY = 3
    QUICK = 4
    POTS_AND_PANS = 5
    SOAK_AND_SCRUB = 6
    LIGHT = 7
    CRYSTAL_CHINA = 8
    RINSE_AND_HOLD = 9
    PLASTICS = 10
    ENERGY = 11
    EXTRA_QUIET = 12


class WashStatus(IntEnum):
    """Dishwasher wash status values."""
    IDLE = 0
    READY = 1
    RUNNING = 2
    PAUSED = 3
    CANCELED = 4
    DRYING = 5
    COMPLETE = 6


class ApplianceType(IntEnum):
    """Known appliance categories derived from appliance_type field.

    Enum values match the category ID (second segment) in the
    appliance_type string, except OVEN which covers categories 3, 4, and 8.
    """
    UNKNOWN = 0
    REFRIGERATOR = 1
    FREEZER = 2
    OVEN = 3          # categories 3 (single), 4 (double), 8 (other)
    WINE_STORAGE = 5
    DISHWASHER = 6

    @classmethod
    def from_type_string(cls, type_str: str) -> ApplianceType:
        """Parse appliance_type like '1.4.2.3' — second segment is category.

        Known category values (from SDDP search types and real devices):
          1 = refrigerator (e.g., 1.1.1.12 = Sub-Zero model 317)
          2 = freezer
          3 = oven single cavity
          4 = oven double cavity (e.g., 1.4.2.3 = Wolf DO30PM)
          5 = wine storage
          6 = dishwasher (e.g., 1.6.1.0 = Cove)
          8 = oven (other variants)
        """
        try:
            parts = type_str.split(".")
            category = int(parts[1]) if len(parts) > 1 else 0
            if category in (3, 4, 8):
                return cls.OVEN
            elif category == 1:
                return cls.REFRIGERATOR
            elif category == 2:
                return cls.FREEZER
            elif category == 5:
                return cls.WINE_STORAGE
            elif category == 6:
                return cls.DISHWASHER
            return cls.UNKNOWN
        except (ValueError, IndexError):
            return cls.UNKNOWN


class ModuleGeneration(IntEnum):
    """Connected module hardware generation.

    Determines which communication paths are available:
      CAT:   Local IP (port 10) + BLE + Cloud
      SABER: BLE + Cloud only (no local IP)
    """
    UNKNOWN = 0
    CAT = 1       # Original module — type string starts with "1."
    SABER = 17    # Newer module — type string starts with "17."

    @classmethod
    def from_type_string(cls, type_str: str) -> ModuleGeneration:
        """Detect module generation from appliance_type string.

        The first segment indicates the module generation:
          1  = CAT (Connected Appliance Technology) — supports local IP on port 10
          17 = Saber/NGIX — cloud and BLE only, no local IP
        """
        try:
            generation = int(type_str.split(".")[0])
            if generation == 1:
                return cls.CAT
            elif generation == 17:
                return cls.SABER
            return cls.UNKNOWN
        except (ValueError, IndexError):
            return cls.UNKNOWN

    @property
    def supports_local_ip(self) -> bool:
        """Whether this module supports direct local IP control on port 10."""
        return self == self.CAT

    @property
    def requires_cloud(self) -> bool:
        """Whether this module requires cloud API for full control."""
        return self != self.CAT


@dataclass
class CavityState:
    """State of a single oven cavity."""
    unit_on: bool = False
    cook_mode: int = 0
    set_temp: int = 0
    temp: int = 0
    at_set_temp: bool = False
    door_ajar: bool = False
    light_on: bool = False
    remote_ready: bool = False
    mode_change_enabled: bool = False
    probe_on: bool = False
    probe_temp: int = 0
    probe_set_temp: int = 0
    probe_at_set_temp: bool = False
    probe_within_10deg: bool = False
    cook_timer_active: bool = False
    cook_timer_complete: bool = False
    cook_timer_within_1min: bool = False
    cook_timer_start_time: str | None = None
    cook_timer_end_time: str | None = None


@dataclass
class KitchenTimerState:
    """State of a kitchen timer."""
    active: bool = False
    complete: bool = False
    within_1min: bool = False
    start_time: str | None = None
    end_time: str | None = None


@dataclass
class RefrigerationState:
    """State of a refrigeration compartment."""
    set_temp: int | None = None
    display_temp: int | None = None
    door_ajar: bool = False


@dataclass
class Appliance:
    """Represents a Sub-Zero Group connected appliance and its current state."""

    # Identity
    host: str = ""
    model: str = ""
    serial: str = ""
    name: str = ""
    appliance_type_raw: str = ""
    appliance_type: ApplianceType = ApplianceType.UNKNOWN
    module_generation: ModuleGeneration = ModuleGeneration.UNKNOWN
    device_wlan_id: str = ""

    # Firmware / API
    api_version: str = ""
    fw_version: str = ""
    uptime: str = ""

    # Network (authenticated only)
    ip_address: str | None = None
    wifi_ssid: str | None = None
    wifi_channel: int | None = None
    wifi_rssi: int | None = None
    cloud_server: str | None = None

    # Global state
    sabbath_on: bool = False
    service_required: bool = False
    energy_event_on: bool = False
    pin_window_open: bool = False
    door_ajar_timeout: int = 5

    # Oven cavities
    cavity1: CavityState = field(default_factory=CavityState)
    cavity2: CavityState = field(default_factory=CavityState)

    # Kitchen timers
    kitchen_timer1: KitchenTimerState = field(default_factory=KitchenTimerState)
    kitchen_timer2: KitchenTimerState = field(default_factory=KitchenTimerState)

    # Refrigeration compartments
    fridge: RefrigerationState = field(default_factory=RefrigerationState)
    fridge2: RefrigerationState = field(default_factory=RefrigerationState)
    freezer: RefrigerationState = field(default_factory=RefrigerationState)
    freezer2: RefrigerationState = field(default_factory=RefrigerationState)

    # Refrigeration features
    ice_maker_on: bool | None = None
    max_ice_on: bool | None = None
    max_ice_start_time: str | None = None
    max_ice_end_time: str | None = None
    night_ice_on: bool | None = None
    light_on: bool | None = None
    accent_light_level: int | None = None
    short_vacation_on: bool | None = None
    long_vacation_on: bool | None = None
    high_use_on: bool | None = None
    high_use_start_time: str | None = None
    high_use_end_time: str | None = None
    emergency_suspend_on: bool | None = None
    air_filter_on: bool | None = None
    air_filter_pct_remaining: int | None = None
    air_filter_end_date: str | None = None
    water_filter_pct_remaining: int | None = None
    water_filter_gal_remaining: int | None = None
    water_filter_end_date: str | None = None

    # Wine
    wine_set_temp: int | None = None
    wine_display_temp: int | None = None

    # Dishwasher
    wash_cycle: int | None = None
    wash_cycle_on: bool | None = None
    wash_status: int | None = None
    wash_cycle_end_time: str | None = None
    door_ajar: bool | None = None
    remote_ready: bool | None = None
    mode: int | None = None
    extended_dry_on: bool | None = None
    heated_dry_on: bool | None = None
    high_temp_wash_on: bool | None = None
    sani_rinse_on: bool | None = None
    top_rack_only_on: bool | None = None
    delay_start_timer_active: bool | None = None
    delay_start_timer_duration: int | None = None
    delay_start_timer_start_time: str | None = None
    delay_start_timer_end_time: str | None = None
    rinse_aid_low: bool | None = None
    softener_low: bool | None = None
    showroom_on: bool | None = None

    # Raw properties dict for anything not explicitly modeled
    raw: dict[str, Any] = field(default_factory=dict)

    def update_from_response(self, resp: dict[str, Any]) -> None:
        """Update appliance state from a response dict.

        For full state responses, replaces all data.
        For delta updates (partial props), merges into existing state.
        """
        # Merge into raw dict rather than replacing, so delta updates
        # don't wipe out properties not included in the update.
        self.raw.update(resp)

        # Identity
        self.model = resp.get("appliance_model", self.model)
        self.serial = resp.get("appliance_serial", self.serial)
        self.name = resp.get("appliance_name", self.name)
        self.device_wlan_id = resp.get("device_wlan_id", self.device_wlan_id)

        type_str = resp.get("appliance_type", "")
        if type_str:
            self.appliance_type_raw = type_str
            self.appliance_type = ApplianceType.from_type_string(type_str)
            self.module_generation = ModuleGeneration.from_type_string(type_str)

        # Version
        version = resp.get("version", {})
        self.api_version = version.get("api", self.api_version)
        self.fw_version = version.get("fw", self.fw_version)
        self.uptime = resp.get("uptime", self.uptime)

        # Network (only present in authenticated responses)
        self.ip_address = resp.get("ipv4_addr", self.ip_address)
        self.wifi_ssid = resp.get("ap_ssid", self.wifi_ssid)
        self.wifi_channel = resp.get("ap_chan", self.wifi_channel)
        self.wifi_rssi = resp.get("ap_rssi", self.wifi_rssi)
        self.cloud_server = resp.get("cloud_server", self.cloud_server)

        # Global
        self.sabbath_on = resp.get("sabbath_on", self.sabbath_on)
        self.service_required = resp.get("service_required", self.service_required)
        self.energy_event_on = resp.get("energy_event_on", self.energy_event_on)
        self.pin_window_open = resp.get("pin_window_open", self.pin_window_open)
        self.door_ajar_timeout = resp.get("door_ajar_timeout", self.door_ajar_timeout)

        # Oven cavities
        self._update_cavity(self.cavity1, resp, "cav_")
        self._update_cavity(self.cavity2, resp, "cav2_")

        # Kitchen timers
        self._update_timer(self.kitchen_timer1, resp, "kitchen_timer_")
        self._update_timer(self.kitchen_timer2, resp, "kitchen_timer2_")

        # Refrigeration
        self._update_ref(self.fridge, resp, "ref_")
        self._update_ref(self.fridge2, resp, "ref2_")
        self._update_ref(self.freezer, resp, "frz_")
        self._update_ref(self.freezer2, resp, "frz2_")

        # Refrigeration features
        self.ice_maker_on = resp.get("ice_maker_on", self.ice_maker_on)
        self.max_ice_on = resp.get("max_ice_on", self.max_ice_on)
        self.max_ice_start_time = resp.get("max_ice_start_time", self.max_ice_start_time)
        self.max_ice_end_time = resp.get("max_ice_end_time", self.max_ice_end_time)
        self.night_ice_on = resp.get("night_ice_on", self.night_ice_on)
        self.light_on = resp.get("light_on", self.light_on)
        self.accent_light_level = resp.get("accent_light_level", self.accent_light_level)
        self.short_vacation_on = resp.get("short_vacation_on", self.short_vacation_on)
        self.long_vacation_on = resp.get("long_vacation_on", self.long_vacation_on)
        self.high_use_on = resp.get("high_use_on", self.high_use_on)
        self.high_use_start_time = resp.get("high_use_start_time", self.high_use_start_time)
        self.high_use_end_time = resp.get("high_use_end_time", self.high_use_end_time)
        self.emergency_suspend_on = resp.get("emergency_suspend_on", self.emergency_suspend_on)
        self.air_filter_on = resp.get("air_filter_on", self.air_filter_on)
        self.air_filter_pct_remaining = resp.get("air_filter_pct_remaining", self.air_filter_pct_remaining)
        self.air_filter_end_date = resp.get("air_filter_end_date", self.air_filter_end_date)
        self.water_filter_pct_remaining = resp.get("water_filter_pct_remaining", self.water_filter_pct_remaining)
        self.water_filter_gal_remaining = resp.get("water_filter_gal_remaining", self.water_filter_gal_remaining)
        self.water_filter_end_date = resp.get("water_filter_end_date", self.water_filter_end_date)

        # Wine
        self.wine_set_temp = resp.get("wine_set_temp", self.wine_set_temp)
        self.wine_display_temp = resp.get("wine_display_temp", self.wine_display_temp)

        # Dishwasher
        self.wash_cycle = resp.get("wash_cycle", self.wash_cycle)
        self.wash_cycle_on = resp.get("wash_cycle_on", self.wash_cycle_on)
        self.wash_status = resp.get("wash_status", self.wash_status)
        self.wash_cycle_end_time = resp.get("wash_cycle_end_time", self.wash_cycle_end_time)
        self.door_ajar = resp.get("door_ajar", self.door_ajar)
        self.remote_ready = resp.get("remote_ready", self.remote_ready)
        self.mode = resp.get("mode", self.mode)
        self.extended_dry_on = resp.get("extended_dry_on", self.extended_dry_on)
        self.heated_dry_on = resp.get("heated_dry_on", self.heated_dry_on)
        self.high_temp_wash_on = resp.get("high_temp_wash_on", self.high_temp_wash_on)
        self.sani_rinse_on = resp.get("sani_rinse_on", self.sani_rinse_on)
        self.top_rack_only_on = resp.get("top_rack_only_on", self.top_rack_only_on)
        self.delay_start_timer_active = resp.get("delay_start_timer_active", self.delay_start_timer_active)
        self.delay_start_timer_duration = resp.get("delay_start_timer_duration", self.delay_start_timer_duration)
        self.delay_start_timer_start_time = resp.get("delay_start_timer_start_time", self.delay_start_timer_start_time)
        self.delay_start_timer_end_time = resp.get("delay_start_timer_end_time", self.delay_start_timer_end_time)
        self.rinse_aid_low = resp.get("rinse_aid_low", self.rinse_aid_low)
        self.softener_low = resp.get("softener_low", self.softener_low)
        self.showroom_on = resp.get("showroom_on", self.showroom_on)

    @staticmethod
    def _update_cavity(cav: CavityState, resp: dict[str, Any], prefix: str) -> None:
        cav.unit_on = resp.get(f"{prefix}unit_on", cav.unit_on)
        cav.cook_mode = resp.get(f"{prefix}cook_mode", cav.cook_mode)
        cav.set_temp = resp.get(f"{prefix}set_temp", cav.set_temp)
        cav.temp = resp.get(f"{prefix}temp", cav.temp)
        cav.at_set_temp = resp.get(f"{prefix}at_set_temp", cav.at_set_temp)
        cav.door_ajar = resp.get(f"{prefix}door_ajar", cav.door_ajar)
        cav.light_on = resp.get(f"{prefix}light_on", cav.light_on)
        cav.remote_ready = resp.get(f"{prefix}remote_ready", cav.remote_ready)
        cav.mode_change_enabled = resp.get(f"{prefix}mode_change_enabled", cav.mode_change_enabled)
        cav.probe_on = resp.get(f"{prefix}probe_on", cav.probe_on)
        cav.probe_temp = resp.get(f"{prefix}probe_temp", cav.probe_temp)
        cav.probe_set_temp = resp.get(f"{prefix}probe_set_temp", cav.probe_set_temp)
        cav.probe_at_set_temp = resp.get(f"{prefix}probe_at_set_temp", cav.probe_at_set_temp)
        cav.probe_within_10deg = resp.get(f"{prefix}probe_within_10deg", cav.probe_within_10deg)
        cav.cook_timer_active = resp.get(f"{prefix}cook_timer_active", cav.cook_timer_active)
        cav.cook_timer_complete = resp.get(f"{prefix}cook_timer_complete", cav.cook_timer_complete)
        cav.cook_timer_within_1min = resp.get(f"{prefix}cook_timer_within_1min", cav.cook_timer_within_1min)
        cav.cook_timer_start_time = resp.get(f"{prefix}cook_timer_start_time", cav.cook_timer_start_time)
        cav.cook_timer_end_time = resp.get(f"{prefix}cook_timer_end_time", cav.cook_timer_end_time)

    @staticmethod
    def _update_timer(timer: KitchenTimerState, resp: dict[str, Any], prefix: str) -> None:
        timer.active = resp.get(f"{prefix}active", timer.active)
        timer.complete = resp.get(f"{prefix}complete", timer.complete)
        timer.within_1min = resp.get(f"{prefix}within_1min", timer.within_1min)
        timer.start_time = resp.get(f"{prefix}start_time", timer.start_time)
        timer.end_time = resp.get(f"{prefix}end_time", timer.end_time)

    @staticmethod
    def _update_ref(ref: RefrigerationState, resp: dict[str, Any], prefix: str) -> None:
        ref.set_temp = resp.get(f"{prefix}set_temp", ref.set_temp)
        ref.display_temp = resp.get(f"{prefix}display_temp", ref.display_temp)
        ref.door_ajar = resp.get(f"{prefix}door_ajar", ref.door_ajar)
