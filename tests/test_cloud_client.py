"""Happy-path and one-failure-each tests for SZGCloudClient public methods.

These tests patch urllib.request.urlopen so they run offline. The
fixture data is modeled on real responses captured in
``tests/test_appliance.py``; we keep it inline rather than loading
from disk to follow the existing repo style.
"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from pyszg import (
    Appliance,
    ApplianceType,
    AuthenticationError,
    SZGCloudAuth,
    SZGCloudClient,
    TokenSet,
)


def _client() -> SZGCloudClient:
    tokens = TokenSet(
        id_token="header.eyJzdWIiOiJ4In0=.sig",
        refresh_token="rt",
        user_id="user-1",
        expires_at=2_000_000_000,
    )
    return SZGCloudClient(tokens, SZGCloudAuth())


def _ok_response(body: dict | str) -> MagicMock:
    """Return a MagicMock that mimics urlopen's response object."""
    if isinstance(body, dict):
        encoded = json.dumps(body).encode()
    else:
        encoded = body.encode()
    resp = MagicMock()
    resp.read.return_value = encoded
    return resp


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_request_parses_json_body(mock_urlopen):
    mock_urlopen.return_value = _ok_response({"hello": "world"})
    assert _client()._request("GET", "/x") == {"hello": "world"}


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_request_returns_raw_for_non_json(mock_urlopen):
    mock_urlopen.return_value = _ok_response("OK")
    assert _client()._request("POST", "/x") == {"_raw": "OK"}


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_request_returns_empty_for_empty_body(mock_urlopen):
    mock_urlopen.return_value = _ok_response("")
    assert _client()._request("POST", "/x") == {}


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_get_devices_returns_device_list(mock_urlopen):
    mock_urlopen.return_value = _ok_response({
        "devices": [
            {"id": "00068002fc90", "applianceId": "1.1.1.12", "name": "Kitchen"},
            {"id": "0006802e7ab2", "applianceId": "17.6.1.1", "name": "Dishwasher"},
        ]
    })
    devices = _client().get_devices()
    assert len(devices) == 2
    assert devices[0]["id"] == "00068002fc90"


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_get_devices_propagates_auth_error(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.HTTPError(
        url="https://x", code=401, msg="auth", hdrs={},
        fp=io.BytesIO(b'{"Message": "Token expired"}'),
    )
    with pytest.raises(AuthenticationError):
        _client().get_devices()


# Real Saber dishwasher response from the existing fixture file.
SABER_DISHWASHER_RESP = {
    "appliance_model": "DW2450WS",
    "appliance_serial": "20145976",
    "appliance_name": "Sub-Zero Connected Appliance",
    "appliance_type": "17.6.1.1",
    "wash_cycle": 0,
    "wash_status": 0,
    "uptime": "244:42:3",
    "version": {"api": "5.5", "fw": "2.27"},
}


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_get_appliance_state_parses_response(mock_urlopen):
    mock_urlopen.return_value = _ok_response({"resp": SABER_DISHWASHER_RESP})
    appliance = _client().get_appliance_state("0006802e7ab2")
    assert isinstance(appliance, Appliance)
    assert appliance.model == "DW2450WS"
    assert appliance.appliance_type == ApplianceType.DISHWASHER


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_get_appliance_state_returns_fresh_instance(mock_urlopen):
    """Library is stateless — two calls produce two distinct objects."""
    mock_urlopen.return_value = _ok_response({"resp": SABER_DISHWASHER_RESP})
    client = _client()
    a = client.get_appliance_state("0006802e7ab2")
    b = client.get_appliance_state("0006802e7ab2")
    assert a is not b


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_get_appliance_state_falls_back_to_get_async(mock_urlopen):
    """CAT modules return 'OK' wrapper for 'get'; client retries with 'get_async'."""
    mock_urlopen.side_effect = [
        _ok_response("OK"),                                # first 'get' returns _raw
        _ok_response({"resp": SABER_DISHWASHER_RESP}),      # 'get_async' returns real data
    ]
    appliance = _client().get_appliance_state("00068006438b")
    assert appliance.model == "DW2450WS"


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_set_property_sends_set_command(mock_urlopen):
    mock_urlopen.return_value = _ok_response({"resp": {"status": 0}})
    resp = _client().set_property("00068002fc90", "ref_set_temp", 37)
    # Inspect what was sent
    call = mock_urlopen.call_args
    sent_request = call.args[0]
    assert sent_request.method == "POST"
    body = json.loads(sent_request.data.decode())
    assert body["pload"]["cmd"] == "set"
    assert body["pload"]["params"] == {"ref_set_temp": 37}


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_send_command_includes_req_id(mock_urlopen):
    mock_urlopen.return_value = _ok_response({"resp": {}})
    _client().send_command("00068002fc90", "open_cloud_async")
    call = mock_urlopen.call_args
    body = json.loads(call.args[0].data.decode())
    assert body["pload"]["cmd"] == "open_cloud_async"
    assert "req_id" in body
    assert len(body["req_id"]) > 0  # uuid4
