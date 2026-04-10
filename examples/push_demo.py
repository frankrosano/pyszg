#!/usr/bin/env python3
"""Demo: real-time push updates from an appliance.

Opens a persistent connection and prints state changes as they happen.
Open/close doors, turn on lights, start cooking — changes appear instantly.

Usage: python push_demo.py <host> <pin>
"""

import sys
from pyszg import SZGClient

if len(sys.argv) < 3:
    print(f"Usage: {sys.argv[0]} <host> <pin>")
    sys.exit(1)

host = sys.argv[1]
pin = sys.argv[2]

client = SZGClient(host, pin=pin)

print(f"Connecting to {host}...")
initial = client.connect_push()
a = client.appliance
print(f"Connected: {a.model} ({a.appliance_type.name})")
print(f"Initial state: {len(initial)} properties")
print(f"\nListening for changes (Ctrl+C to stop)...\n")

try:
    for update in client.push_updates():
        ts = update.get("timestamp", "")
        seq = update.get("seq", "")
        props = update.get("props", {})
        for key, value in props.items():
            print(f"  [{ts}] #{seq} {key} = {value}")
except KeyboardInterrupt:
    print("\nStopped.")
finally:
    client.disconnect_push()
