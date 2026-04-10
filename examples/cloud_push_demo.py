#!/usr/bin/env python3
"""Demo: real-time cloud push updates via SignalR for all appliances.

Usage:
    python cloud_push_demo.py [--reauth]
"""

import asyncio
import os
import signal
import sys
from pyszg import SZGCloudAuth, SZGCloudSignalR

TOKEN_FILE = "cloud_tokens.json"


def on_update(device_id, msg_type, data):
    """Called for each SignalR update."""
    short_id = device_id[:16]
    if msg_type == 1:
        count = len([k for k in data if k not in ("notifs", "service", "version")])
        print(f"  {short_id} — full state ({count} properties)")
    elif msg_type == 2:
        props = data.get("props", data)
        for key, value in props.items():
            if key not in ("msg_types", "seq", "timestamp"):
                print(f"  {short_id} — {key} = {value}")


async def main():
    auth = SZGCloudAuth()

    if os.path.exists(TOKEN_FILE) and "--reauth" not in sys.argv:
        tokens = auth.load_tokens(TOKEN_FILE)
        tokens = auth.ensure_valid(tokens)
        auth.save_tokens(tokens, TOKEN_FILE)
    else:
        tokens = auth.login()
        auth.save_tokens(tokens, TOKEN_FILE)

    print(f"Authenticated as: {tokens.name}")
    print(f"Listening for real-time updates (Ctrl+C to stop)...\n")

    signalr = SZGCloudSignalR(tokens, auth)

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, lambda: asyncio.ensure_future(signalr.disconnect()))

    await signalr.connect(callback=on_update)
    print("\nDisconnected.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
