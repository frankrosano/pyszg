"""Tests for Appliance model parsing."""

from pyszg.appliance import Appliance, ApplianceType, CavityState, ModuleGeneration


# Real response captured from a Wolf DO30PM double oven (authenticated)
SAMPLE_RESPONSE = {
    "diagnostic_status": "0x11111111111",
    "service": {},
    "version": {"api": "5.4", "fw": "8.5", "appliance": "Unknown"},
    "appliance_name": "SubZeroCAT",
    "appliance_type": "1.4.2.3",
    "appliance_model": "DO30PM",
    "appliance_serial": "17480342",
    "device_wlan_id": "00068006438b",
    "ipv4_addr": "10.105.5.50",
    "ap_ssid": "RosanoNet-IoT",
    "ap_chan": 149,
    "ap_rssi": 18,
    "cloud_server": "prod",
    "uptime": "435:53:58",
    "sabbath_on": False,
    "service_required": False,
    "pin_window_open": True,
    "door_ajar_timeout": 5,
    "energy_event_on": False,
    "cav_unit_on": False,
    "cav_cook_mode": 0,
    "cav_set_temp": 0,
    "cav_temp": 75,
    "cav_at_set_temp": False,
    "cav_door_ajar": False,
    "cav_light_on": False,
    "cav_remote_ready": False,
    "cav_mode_change_enabled": False,
    "cav_probe_on": False,
    "cav_probe_temp": 0,
    "cav_probe_set_temp": 0,
    "cav_probe_at_set_temp": False,
    "cav_probe_within_10deg": False,
    "cav_cook_timer_active": False,
    "cav_cook_timer_complete": False,
    "cav_cook_timer_within_1min": False,
    "cav_cook_timer_start_time": None,
    "cav_cook_timer_end_time": None,
    "cav2_unit_on": False,
    "cav2_cook_mode": 0,
    "cav2_set_temp": 0,
    "cav2_temp": 68,
    "cav2_door_ajar": False,
    "cav2_light_on": False,
}


def test_parse_oven_response():
    a = Appliance()
    a.update_from_response(SAMPLE_RESPONSE)

    assert a.model == "DO30PM"
    assert a.serial == "17480342"
    assert a.name == "SubZeroCAT"
    assert a.appliance_type == ApplianceType.OVEN
    assert a.module_generation == ModuleGeneration.CAT
    assert a.module_generation.supports_local_ip is True
    assert a.api_version == "5.4"
    assert a.fw_version == "8.5"
    assert a.ip_address == "10.105.5.50"
    assert a.wifi_ssid == "RosanoNet-IoT"
    assert a.sabbath_on is False

    assert a.cavity1.temp == 75
    assert a.cavity1.unit_on is False
    assert a.cavity1.door_ajar is False
    assert a.cavity1.light_on is False

    assert a.cavity2.temp == 68
    assert a.cavity2.unit_on is False


def test_parse_minimal_response():
    """Unauthenticated response has fewer fields."""
    minimal = {
        "diagnostic_status": "0x11111111111",
        "service": {},
        "version": {"api": "5.4", "fw": "8.5", "appliance": "Unknown"},
        "appliance_name": "SubZeroCAT",
        "appliance_type": "1.4.2.3",
        "appliance_model": "DO30PM",
        "cav_door_ajar": False,
        "cav_unit_on": False,
        "cav2_door_ajar": False,
        "cav2_unit_on": False,
        "uptime": "434:34:33",
        "device_wlan_id": "00068006438b",
    }
    a = Appliance()
    a.update_from_response(minimal)

    assert a.model == "DO30PM"
    assert a.appliance_type == ApplianceType.OVEN
    assert a.cavity1.door_ajar is False
    # Fields not in minimal response retain defaults
    assert a.cavity1.temp == 0
    assert a.ip_address is None


def test_appliance_type_parsing():
    # Ovens
    assert ApplianceType.from_type_string("1.4.2.3") == ApplianceType.OVEN
    assert ApplianceType.from_type_string("1.3.1.1") == ApplianceType.OVEN
    assert ApplianceType.from_type_string("1.8.1.0") == ApplianceType.OVEN
    # Refrigerator
    assert ApplianceType.from_type_string("1.1.1.12") == ApplianceType.REFRIGERATOR
    # Freezer
    assert ApplianceType.from_type_string("1.2.1.0") == ApplianceType.FREEZER
    # Wine
    assert ApplianceType.from_type_string("1.5.1.0") == ApplianceType.WINE_STORAGE
    # Dishwasher
    assert ApplianceType.from_type_string("1.6.1.0") == ApplianceType.DISHWASHER
    assert ApplianceType.from_type_string("17.6.1.1") == ApplianceType.DISHWASHER
    # Invalid
    assert ApplianceType.from_type_string("") == ApplianceType.UNKNOWN
    assert ApplianceType.from_type_string("garbage") == ApplianceType.UNKNOWN


def test_module_generation():
    # CAT modules
    assert ModuleGeneration.from_type_string("1.1.1.12") == ModuleGeneration.CAT
    assert ModuleGeneration.from_type_string("1.4.2.3") == ModuleGeneration.CAT
    assert ModuleGeneration.from_type_string("1.6.1.0") == ModuleGeneration.CAT
    # Saber modules
    assert ModuleGeneration.from_type_string("17.6.1.1") == ModuleGeneration.SABER
    # Properties
    assert ModuleGeneration.CAT.supports_local_ip is True
    assert ModuleGeneration.CAT.requires_cloud is False
    assert ModuleGeneration.SABER.supports_local_ip is False
    assert ModuleGeneration.SABER.requires_cloud is True
    # Invalid
    assert ModuleGeneration.from_type_string("") == ModuleGeneration.UNKNOWN
    assert ModuleGeneration.from_type_string("99.1.1.1") == ModuleGeneration.UNKNOWN


# Real response captured from a Sub-Zero 317 refrigerator (authenticated)
FRIDGE_RESPONSE = {
    "ap_enc": 3,
    "ipv4_addr": "10.105.5.251",
    "ap_rssi": 33,
    "version": {
        "api": "5.4",
        "fw": "8.5",
        "appliance": "main: 3.12.0; conf: 99.3.10; uim: 2.1.0",
    },
    "diagnostic_status": "0x11111111111",
    "time": "2026-04-01T15:46-04:00",
    "ap_ssid": "RosanoNet-IoT",
    "ap_chan": 149,
    "uptime": "437:22:09",
    "device_wlan_id": "00068002fc90",
    "cloud_server": "prod",
    "service_mode": 0,
    "service": {},
    "pin_window_open": True,
    "appliance_name": "SubZeroCAT",
    "appliance_type": "1.1.1.12",
    "appliance_model": "317",
    "appliance_serial": "4543466",
    "remote_svc_reg_token": None,
    "door_ajar_timeout": 1,
    "sabbath_on": False,
    "service_required": False,
    "air_filter_end_date": "2161-12-21",
    "air_filter_on": True,
    "air_filter_pct_remaining": 0,
    "water_filter_end_date": "2161-12-23",
    "water_filter_gal_remaining": -172,
    "water_filter_pct_remaining": 0,
    "accent_light_level": 0,
    "emergency_suspend_on": False,
    "high_use_on": False,
    "long_vacation_on": False,
    "short_vacation_on": False,
    "ref_door_ajar": False,
    "ref_set_temp": 38,
    "frz_door_ajar": False,
    "frz_set_temp": 0,
    "ice_maker_on": True,
    "max_ice_on": False,
    "max_ice_start_time": None,
    "max_ice_end_time": None,
    "night_ice_on": False,
}


def test_parse_fridge_response():
    a = Appliance()
    a.update_from_response(FRIDGE_RESPONSE)

    assert a.model == "317"
    assert a.serial == "4543466"
    assert a.appliance_type == ApplianceType.REFRIGERATOR
    assert a.module_generation == ModuleGeneration.CAT
    assert a.appliance_type_raw == "1.1.1.12"

    # Refrigeration compartments
    assert a.fridge.set_temp == 38
    assert a.fridge.door_ajar is False
    assert a.freezer.set_temp == 0
    assert a.freezer.door_ajar is False

    # Refrigeration features
    assert a.ice_maker_on is True
    assert a.max_ice_on is False
    assert a.night_ice_on is False
    assert a.short_vacation_on is False
    assert a.long_vacation_on is False
    assert a.emergency_suspend_on is False
    assert a.high_use_on is False
    assert a.accent_light_level == 0

    # Filters
    assert a.air_filter_on is True
    assert a.air_filter_pct_remaining == 0
    assert a.air_filter_end_date == "2161-12-21"
    assert a.water_filter_gal_remaining == -172
    assert a.water_filter_pct_remaining == 0

    # Oven fields should remain at defaults
    assert a.cavity1.temp == 0
    assert a.cavity1.unit_on is False


# Real response captured from a Cove DW2450WS dishwasher via cloud API (Saber module)
DISHWASHER_RESPONSE = {
    "notifs": [],
    "diagnostic_status": "0x11111111111",
    "pin_window_open": True,
    "delay_start_timer_active": False,
    "delay_start_timer_duration": 0,
    "delay_start_timer_end_time": None,
    "delay_start_timer_start_time": None,
    "door_ajar": False,
    "extended_dry_on": False,
    "heated_dry_on": False,
    "high_temp_wash_on": False,
    "light_on": False,
    "mode": 0,
    "remote_ready": False,
    "rinse_aid_low": False,
    "sani_rinse_on": False,
    "service_required": False,
    "showroom_on": False,
    "top_rack_only_on": False,
    "wash_cycle": 0,
    "wash_cycle_end_time": None,
    "wash_cycle_on": False,
    "wash_status": 0,
    "softener_low": True,
    "ap_chan": 6,
    "ap_enc": 3,
    "ap_rssi": -70,
    "ap_ssid": "RosanoNet-IoT",
    "appliance_model": "DW2450WS",
    "appliance_name": "Sub-Zero Connected Appliance",
    "appliance_serial": "20145976",
    "appliance_type": "17.6.1.1",
    "build_info": {"desc": "2.27", "build_date": "2024-11-26T16:04:29"},
    "device_wlan_id": "0006802e7ab2",
    "door_ajar_timeout": 5,
    "ipv4_addr": "10.105.5.234",
    "service": {},
    "service_mode": 0,
    "smart_grid_on": None,
    "time": "2026-04-03T20:54:04+00:00",
    "uptime": "244:42:3",
    "version": {
        "appliance": "uim: 21.2.31; uim_slave: 7.1.0; main: 17.6.0; wash_pump: 12.1.0; rs485: 10.3.0",
        "architecture": "",
        "fw": "2.27",
        "rtapp": "2.27",
        "os": "25.10",
        "bleapp": "3.4",
        "api": "5.5",
    },
}


def test_parse_dishwasher_response():
    a = Appliance()
    a.update_from_response(DISHWASHER_RESPONSE)

    # Identity
    assert a.model == "DW2450WS"
    assert a.serial == "20145976"
    assert a.name == "Sub-Zero Connected Appliance"
    assert a.appliance_type == ApplianceType.DISHWASHER
    assert a.module_generation == ModuleGeneration.SABER
    assert a.module_generation.supports_local_ip is False
    assert a.module_generation.requires_cloud is True
    assert a.appliance_type_raw == "17.6.1.1"

    # Firmware
    assert a.api_version == "5.5"
    assert a.fw_version == "2.27"

    # Network
    assert a.ip_address == "10.105.5.234"
    assert a.wifi_ssid == "RosanoNet-IoT"

    # Dishwasher properties
    assert a.wash_cycle_on is False
    assert a.wash_cycle == 0
    assert a.wash_status == 0
    assert a.door_ajar is False
    assert a.remote_ready is False
    assert a.mode == 0
    assert a.extended_dry_on is False
    assert a.heated_dry_on is False
    assert a.high_temp_wash_on is False
    assert a.sani_rinse_on is False
    assert a.top_rack_only_on is False
    assert a.delay_start_timer_active is False
    assert a.rinse_aid_low is False
    assert a.softener_low is True
    assert a.showroom_on is False
    assert a.light_on is False

    # Oven/fridge fields should remain at defaults
    assert a.cavity1.temp == 0
    assert a.fridge.set_temp is None
    assert a.ice_maker_on is None
