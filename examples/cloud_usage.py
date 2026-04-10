#!/usr/bin/env python3
"""Cloud API usage example.

First run (interactive login):
    python cloud_usage.py

Subsequent runs (uses saved tokens):
    python cloud_usage.py

Force re-login:
    python cloud_usage.py --reauth
"""

import json
import os
import sys
from pyszg import SZGCloudAuth, SZGCloudClient, ApplianceType

TOKEN_FILE = "cloud_tokens.json"


def main():
    auth = SZGCloudAuth()

    # Load or create tokens
    if os.path.exists(TOKEN_FILE) and "--reauth" not in sys.argv:
        print("Loading saved tokens...")
        tokens = auth.load_tokens(TOKEN_FILE)
        tokens = auth.ensure_valid(tokens)
        auth.save_tokens(tokens, TOKEN_FILE)
    else:
        print("Starting browser login...")
        tokens = auth.login()
        auth.save_tokens(tokens, TOKEN_FILE)

    print(f"Authenticated as: {tokens.name} ({tokens.email})")
    print(f"User ID: {tokens.user_id}\n")

    # Create cloud client
    client = SZGCloudClient(tokens, auth)

    # List devices
    print("=== Registered Appliances ===")
    devices = client.get_devices()
    for dev in devices:
        name = dev.get("name") or "(unnamed)"
        print(f"  {dev['id'][:16]:16s}  type={dev['applianceId']:12s}  {name}")

    # Get state for each device
    for dev in devices:
        device_id = dev["id"]
        print(f"\n=== {dev.get('name') or dev['applianceId']} ({device_id[:16]}) ===")
        try:
            appliance = client.get_appliance_state(device_id)
            print(f"  Model: {appliance.model}")
            print(f"  Type: {appliance.appliance_type.name}")
            print(f"  Uptime: {appliance.uptime}")

            if appliance.appliance_type == ApplianceType.OVEN:
                print(f"  Upper: {appliance.cavity1.temp}°F, on={appliance.cavity1.unit_on}")
                print(f"  Lower: {appliance.cavity2.temp}°F, on={appliance.cavity2.unit_on}")
            elif appliance.appliance_type == ApplianceType.REFRIGERATOR:
                print(f"  Fridge: set={appliance.fridge.set_temp}°F")
                print(f"  Freezer: set={appliance.freezer.set_temp}°F")
                print(f"  Ice maker: {appliance.ice_maker_on}")
            elif appliance.appliance_type == ApplianceType.DISHWASHER:
                print(f"  Wash cycle on: {appliance.wash_cycle_on}")
                print(f"  Door ajar: {appliance.door_ajar}")
                print(f"  Rinse aid low: {appliance.rinse_aid_low}")
                print(f"  Softener low: {appliance.softener_low}")
                print(f"  Remote ready: {appliance.remote_ready}")

            if not appliance.model:
                print(f"  (CAT module — use local IP for full state)")
        except Exception as e:
            print(f"  Error: {e}")


if __name__ == "__main__":
    main()
