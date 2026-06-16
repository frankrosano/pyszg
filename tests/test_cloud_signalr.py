"""Tests for the pure SignalR helpers: the triple-nested message parser and
the JWT expiry reader. No network or WebSocket involved.
"""

from __future__ import annotations

import base64
import json

import pytest

# SignalR module requires the optional `websockets` dependency.
pytest.importorskip("websockets")

from pyszg.cloud_signalr import _get_token_expiry, _parse_signalr_message


def _make_jwt(claims: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


def _connected_appliance_message(device_id: str, channel: dict) -> dict:
    """Build the triple-JSON-nested ConnectedApplianceMessage envelope."""
    outer = {
        "DeviceId": device_id,
        "Payload": json.dumps({"api.async_channel": json.dumps(channel)}),
    }
    return {
        "type": 1,
        "target": "ConnectedApplianceMessage",
        "arguments": [json.dumps(outer)],
    }


# --- _get_token_expiry ---------------------------------------------------


def test_get_token_expiry_reads_exp():
    assert _get_token_expiry(_make_jwt({"exp": 1700000000})) == 1700000000.0


def test_get_token_expiry_returns_zero_on_garbage():
    assert _get_token_expiry("garbage") == 0
    assert _get_token_expiry(_make_jwt({"no_exp": 1})) == 0


# --- _parse_signalr_message ---------------------------------------------


def test_parse_full_state_message():
    msg = _connected_appliance_message(
        "dev1", {"type": 1, "pload": {"appliance_model": "DW2450WS"}}
    )
    parsed = _parse_signalr_message(msg)
    assert parsed == {
        "device_id": "dev1",
        "msg_type": 1,
        "data": {"appliance_model": "DW2450WS"},
    }


def test_parse_delta_message():
    msg = _connected_appliance_message(
        "dev2", {"type": 2, "pload": {"props": {"wash_status": 2}}}
    )
    parsed = _parse_signalr_message(msg)
    assert parsed["device_id"] == "dev2"
    assert parsed["msg_type"] == 2
    assert parsed["data"] == {"props": {"wash_status": 2}}


def test_parse_ignores_non_appliance_message():
    # Ping frame (type 6) is not a ConnectedApplianceMessage.
    assert _parse_signalr_message({"type": 6}) is None
    # Right type, wrong target.
    assert _parse_signalr_message(
        {"type": 1, "target": "SomethingElse", "arguments": ["{}"]}
    ) is None


def test_parse_handles_malformed_arguments():
    # arguments[0] is not valid JSON -> None, not an exception.
    bad = {"type": 1, "target": "ConnectedApplianceMessage", "arguments": ["not json"]}
    assert _parse_signalr_message(bad) is None
    # Empty arguments.
    empty = {"type": 1, "target": "ConnectedApplianceMessage", "arguments": []}
    assert _parse_signalr_message(empty) is None
