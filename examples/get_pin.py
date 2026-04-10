#!/usr/bin/env python3
"""Request an appliance to display its 6-digit pairing PIN.

The PIN will appear on the appliance's physical display/screen.
A door on the appliance must be physically open before running this.

Usage:
    python get_pin.py <host>

Examples:
    python get_pin.py 192.168.1.100
"""

import sys
from pyszg import SZGClient
from pyszg.exceptions import CommandError


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <host>")
        print(f"\nRequests the appliance to show its 6-digit PIN on its display.")
        print(f"NOTE: A door on the appliance must be physically open first.")
        sys.exit(1)

    host = sys.argv[1]
    client = SZGClient(host)

    # Check current state to give helpful feedback
    print(f"Connecting to {host}...")
    try:
        client.refresh_minimal()
    except Exception as e:
        print(f"Error: Could not connect to {host} — {e}")
        sys.exit(1)

    a = client.appliance
    print(f"Found: {a.name} ({a.model})")

    # Check door state if available (oven cavities)
    if a.cavity1.door_ajar or a.cavity2.door_ajar:
        print(f"Door is open — good.")
    else:
        print(f"Tip: A door must be physically open for the PIN to display.")

    # Send display_pin command
    print(f"\nRequesting PIN display (20 seconds)...")
    try:
        client.display_pin(duration=20)
        print(f"\n✓ Check the appliance display now — the 6-digit PIN should be visible.")
        print(f"  You have 20 seconds to read it.")
    except CommandError as e:
        if e.status == 101:
            print(f"\n✗ The appliance refused the request.")
            print(f"  Open a door on the appliance and try again.")
        else:
            print(f"\n✗ Error: {e}")


if __name__ == "__main__":
    main()
