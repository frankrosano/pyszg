"""Tests for the local transport: CAT response-status classification and the
PIN guards on SZGClient. These exercise the pure logic paths and the
pre-connect validation, so no socket is opened.
"""

from __future__ import annotations

import pytest

from pyszg import (
    AuthenticationError,
    CommandError,
    SZGClient,
    SZGConnectionError,
)
from pyszg.connection import (
    CATConnection,
    STATUS_OK,
    STATUS_OUT_OF_RANGE,
    STATUS_UNKNOWN_COMMAND,
)


# --- CATConnection._check_response status mapping ------------------------


def test_check_response_ok_returns_resp():
    conn = CATConnection("10.0.0.1")
    out = conn._check_response({"status": STATUS_OK, "resp": {"k": "v"}})
    assert out == {"k": "v"}


def test_check_response_lockout_maps_to_auth_error():
    conn = CATConnection("10.0.0.1")
    resp = {"status": STATUS_OUT_OF_RANGE, "resp": {"lockout_duration": 30}}
    with pytest.raises(AuthenticationError):
        conn._check_response(resp, "unlock_channel")


def test_check_response_out_of_range_without_lockout_is_command_error():
    conn = CATConnection("10.0.0.1")
    with pytest.raises(CommandError):
        conn._check_response({"status": STATUS_OUT_OF_RANGE, "resp": {}})


def test_check_response_unknown_command_is_command_error():
    conn = CATConnection("10.0.0.1")
    with pytest.raises(CommandError):
        conn._check_response({"status": STATUS_UNKNOWN_COMMAND, "status_msg": "nope"})


# --- PIN validation / guards (no network) --------------------------------


def test_execute_rejects_malformed_pin_before_connecting():
    conn = CATConnection("10.0.0.1")
    # Validation happens before any socket work, so this never touches the net.
    with pytest.raises(ValueError):
        conn.execute({"cmd": "get_async"}, pin="12")


def test_set_property_requires_pin():
    client = SZGClient("10.0.0.1")  # no PIN
    with pytest.raises(ValueError):
        client.set_property("cav_light_on", True)


def test_connect_push_requires_pin():
    client = SZGClient("10.0.0.1")
    with pytest.raises(ValueError):
        client.connect_push()


def test_read_update_without_connection_raises():
    client = SZGClient("10.0.0.1", pin="123456")
    with pytest.raises(SZGConnectionError):
        client.read_update()
