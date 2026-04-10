#!/usr/bin/env python3
"""Basic pyszg usage examples.

Usage:
    python basic_usage.py <host> [pin]

Examples:
    python basic_usage.py 10.105.5.50
    python basic_usage.py 10.105.5.50 635412
"""

import sys
from pyszg import SZGClient, ApplianceType


def print_oven(a):
    """Print oven-specific state."""
    print(f"  Upper cavity: {a.cavity1.temp}°F, on={a.cavity1.unit_on}, "
          f"light={a.cavity1.light_on}, door={a.cavity1.door_ajar}")
    print(f"  Lower cavity: {a.cavity2.temp}°F, on={a.cavity2.unit_on}, "
          f"light={a.cavity2.light_on}, door={a.cavity2.door_ajar}")
    if a.cavity1.cook_mode:
        print(f"  Upper cook mode: {a.cavity1.cook_mode}, set={a.cavity1.set_temp}°F")
    if a.cavity2.cook_mode:
        print(f"  Lower cook mode: {a.cavity2.cook_mode}, set={a.cavity2.set_temp}°F")
    if a.cavity1.probe_on:
        print(f"  Upper probe: {a.cavity1.probe_temp}°F (target {a.cavity1.probe_set_temp}°F)")
    print(f"  Sabbath: {a.sabbath_on}")


def print_fridge(a):
    """Print refrigerator-specific state."""
    print(f"  Fridge: set={a.fridge.set_temp}°F, door={a.fridge.door_ajar}")
    if a.freezer.set_temp is not None:
        print(f"  Freezer: set={a.freezer.set_temp}°F, door={a.freezer.door_ajar}")
    if a.fridge2.set_temp is not None:
        print(f"  Fridge 2: set={a.fridge2.set_temp}°F, door={a.fridge2.door_ajar}")
    if a.ice_maker_on is not None:
        print(f"  Ice maker: {a.ice_maker_on}, max_ice={a.max_ice_on}, night_ice={a.night_ice_on}")
    if a.short_vacation_on is not None:
        print(f"  Vacation: short={a.short_vacation_on}, long={a.long_vacation_on}")
    if a.air_filter_pct_remaining is not None:
        print(f"  Air filter: {a.air_filter_pct_remaining}% remaining")
    if a.water_filter_pct_remaining is not None:
        print(f"  Water filter: {a.water_filter_pct_remaining}% remaining "
              f"({a.water_filter_gal_remaining} gal)")
    if a.accent_light_level is not None:
        print(f"  Accent light level: {a.accent_light_level}")
    print(f"  Sabbath: {a.sabbath_on}")


def print_generic(a):
    """Print state for unknown/other appliance types."""
    print(f"  Properties: {len(a.raw)} total")
    for key in sorted(a.raw.keys()):
        if key in ("version", "service", "notifs", "diagnostic_status"):
            continue
        print(f"    {key}: {a.raw[key]}")


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <host> [pin]")
        sys.exit(1)

    host = sys.argv[1]
    pin = sys.argv[2] if len(sys.argv) > 2 else None

    client = SZGClient(host, pin=pin)

    # --- Read state ---
    if pin:
        print("=== Full read (authenticated) ===")
        client.refresh()
    else:
        print("=== Minimal read (no auth) ===")
        client.refresh_minimal()
        print("  (Pass PIN as second argument for full property set)\n")

    a = client.appliance
    print(f"  Model: {a.model} (type: {a.appliance_type.name})")
    if a.serial:
        print(f"  Serial: {a.serial}")
    print(f"  Firmware: API {a.api_version}, FW {a.fw_version}")
    print(f"  Uptime: {a.uptime}")
    if a.wifi_ssid:
        print(f"  WiFi: {a.wifi_ssid} (ch {a.wifi_channel}, rssi {a.wifi_rssi})")
    print()

    if a.appliance_type == ApplianceType.OVEN:
        print_oven(a)
    elif a.appliance_type in (ApplianceType.REFRIGERATOR, ApplianceType.FREEZER):
        print_fridge(a)
    else:
        print_generic(a)


if __name__ == "__main__":
    main()
