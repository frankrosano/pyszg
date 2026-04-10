#!/usr/bin/env python3
"""Dump all properties from an appliance for debugging.

Usage:
    Local:  python dump_state.py <host> [pin]
    Cloud:  python dump_state.py --cloud [device_id]
"""

import json
import os
import sys


def dump_local(host, pin=None):
    from pyszg import SZGClient

    client = SZGClient(host, pin=pin)
    if pin:
        client.refresh()
    else:
        client.refresh_minimal()

    a = client.appliance
    print(f"Host: {host}")
    print(f"Model: {a.model}")
    print(f"Serial: {a.serial}")
    print(f"Type: {a.appliance_type.name} ({a.appliance_type_raw})")
    print(f"Module: {a.module_generation.name}")
    print(f"Authenticated: {'yes' if pin else 'no'}")
    print(f"Properties: {len(a.raw)}")
    print()
    for k in sorted(a.raw.keys()):
        if k in ("notifs", "service", "version", "build_info"):
            continue
        print(f"  {k}: {a.raw[k]!r}")

    # Print nested objects separately
    if "version" in a.raw:
        print(f"\n  version:")
        for vk, vv in a.raw["version"].items():
            print(f"    {vk}: {vv}")


def dump_cloud(device_id=None):
    from pyszg import SZGCloudAuth, SZGCloudClient

    token_file = "cloud_tokens.json"
    if not os.path.exists(token_file):
        print(f"No {token_file} found. Run cloud_usage.py first to authenticate.")
        sys.exit(1)

    auth = SZGCloudAuth()
    tokens = auth.load_tokens(token_file)
    tokens = auth.ensure_valid(tokens)
    auth.save_tokens(tokens, token_file)

    client = SZGCloudClient(tokens, auth)
    devices = client.get_devices()

    targets = devices
    if device_id:
        targets = [d for d in devices if d["id"].startswith(device_id)]
        if not targets:
            print(f"No device found matching '{device_id}'")
            print("Available devices:")
            for d in devices:
                print(f"  {d['id'][:16]}  {d.get('name') or d['applianceId']}")
            sys.exit(1)

    for dev in targets:
        did = dev["id"]
        name = dev.get("name") or dev["applianceId"]
        print(f"{'='*60}")
        print(f"Device: {name}")
        print(f"ID: {did}")
        print(f"Type: {dev.get('applianceId')}")
        print(f"MAC: {dev.get('macId')}")
        print(f"BDA: {dev.get('bda')}")
        print()

        try:
            appliance = client.get_appliance_state(did)
            print(f"Model: {appliance.model}")
            print(f"Serial: {appliance.serial}")
            print(f"Module: {appliance.module_generation.name}")
            print(f"Properties: {len(appliance.raw)}")
            print()
            for k in sorted(appliance.raw.keys()):
                if k in ("notifs", "service", "version", "build_info"):
                    continue
                print(f"  {k}: {appliance.raw[k]!r}")

            if "version" in appliance.raw:
                print(f"\n  version:")
                for vk, vv in appliance.raw["version"].items():
                    print(f"    {vk}: {vv}")
        except Exception as e:
            print(f"  Error: {e}")

        print()


def main():
    if len(sys.argv) < 2:
        print(f"Usage:")
        print(f"  Local:  {sys.argv[0]} <host> [pin]")
        print(f"  Cloud:  {sys.argv[0]} --cloud [device_id_prefix]")
        sys.exit(1)

    if sys.argv[1] == "--cloud":
        device_id = sys.argv[2] if len(sys.argv) > 2 else None
        dump_cloud(device_id)
    else:
        host = sys.argv[1]
        pin = sys.argv[2] if len(sys.argv) > 2 else None
        dump_local(host, pin)


if __name__ == "__main__":
    main()
