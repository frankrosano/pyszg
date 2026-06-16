"""Socket-level tests for the local CAT transport.

The SSL-wrapped socket is mocked (``_create_ssl_context`` +
``socket.create_connection``) so the real send/recv/framing logic in
``connection.py`` runs without opening a network connection.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from pyszg.connection import CATConnection, CATStreamConnection
from pyszg.exceptions import (
    AuthenticationError,
    CommandError,
    SZGConnectionError,
)


@contextmanager
def _mocked_socket(recv_values):
    """Patch the connection layer so wrap_socket returns a fake socket whose
    ``recv`` yields the given byte chunks in order. Yields that socket so the
    test can assert on ``sendall`` / ``close`` calls.
    """
    sock = MagicMock()
    sock.recv.side_effect = list(recv_values)
    ctx = MagicMock()
    ctx.wrap_socket.return_value = sock
    with patch("pyszg.connection._create_ssl_context", return_value=ctx), patch(
        "pyszg.connection.socket.create_connection", return_value=MagicMock()
    ):
        yield sock


# --- CATConnection.execute (request/response) ---------------------------


def test_execute_single_command_returns_resp():
    with _mocked_socket([b'{"status":0,"resp":{"foo":"bar"}}']) as sock:
        out = CATConnection("10.0.0.1").execute({"cmd": "get_async"})
    assert out == {"foo": "bar"}
    sock.sendall.assert_called_once()
    sock.close.assert_called_once()  # connection closed in finally


def test_execute_with_pin_unlocks_then_sends_command():
    recv = [
        b'{"status":0,"resp":{}}',                # unlock_channel OK
        b'{"status":0,"resp":{"done":true}}',     # actual command OK
    ]
    with _mocked_socket(recv) as sock:
        out = CATConnection("10.0.0.1").execute(
            {"cmd": "set", "params": {"x": 1}}, pin="123456"
        )
    assert out == {"done": True}
    assert sock.sendall.call_count == 2
    # The first thing sent must be the unlock handshake.
    first_payload = sock.sendall.call_args_list[0].args[0]
    assert b"unlock_channel" in first_payload


def test_execute_pin_lockout_raises_auth_and_skips_command():
    recv = [b'{"status":3,"resp":{"lockout_duration":30}}']  # OUT_OF_RANGE + lockout
    with _mocked_socket(recv) as sock:
        conn = CATConnection("10.0.0.1")
        with pytest.raises(AuthenticationError):
            conn.execute({"cmd": "set"}, pin="123456")
    # Only the unlock was sent; the command must not go out after a rejected PIN.
    assert sock.sendall.call_count == 1
    sock.close.assert_called_once()


def test_execute_command_error_status_maps_to_command_error():
    with _mocked_socket([b'{"status":5,"status_msg":"unknown cmd"}']):
        with pytest.raises(CommandError):
            CATConnection("10.0.0.1").execute({"cmd": "bogus"})


def test_execute_connection_closed_raises():
    with _mocked_socket([b""]):  # empty recv => peer closed
        with pytest.raises(SZGConnectionError):
            CATConnection("10.0.0.1").execute({"cmd": "get_async"})


def test_execute_invalid_json_raises_connection_error():
    with _mocked_socket([b"not json at all"]):
        with pytest.raises(SZGConnectionError):
            CATConnection("10.0.0.1").execute({"cmd": "get_async"})


# --- CATStreamConnection (persistent push) ------------------------------


def test_stream_connect_returns_initial_state():
    recv = [
        b'{"status":0,"resp":{}}\n',                              # unlock OK
        b'{"status":0,"resp":{"appliance_model":"DW2450WS"}}\n',  # get_async snapshot
    ]
    with _mocked_socket(recv):
        stream = CATStreamConnection("10.0.0.1", pin="123456")
        initial = stream.connect()
    assert initial == {"appliance_model": "DW2450WS"}
    assert stream.connected is True


def test_stream_connect_pin_rejected_raises_auth():
    recv = [b'{"status":3,"resp":{"lockout_duration":30}}\n']
    with _mocked_socket(recv):
        stream = CATStreamConnection("10.0.0.1", pin="123456")
        with pytest.raises(AuthenticationError):
            stream.connect()
    assert stream.connected is False  # closed on failure


def test_stream_connect_requires_pin():
    stream = CATStreamConnection("10.0.0.1", pin="")
    with pytest.raises(ValueError):
        stream.connect()


def test_stream_read_update_parses_buffered_line():
    """A complete line already in the buffer is returned without a socket read."""
    stream = CATStreamConnection("10.0.0.1", pin="123456")
    stream._sock = MagicMock()
    stream._buffer = b'{"msg_types":2,"props":{"cav_door_ajar":true}}\n'
    update = stream.read_update(timeout=0.1)
    assert update == {"msg_types": 2, "props": {"cav_door_ajar": True}}
    stream._sock.recv.assert_not_called()
