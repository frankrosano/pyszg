"""Tests for the urllib → library exception mapping in SZGCloudClient.

Covers each row of the mapping table from design.md:

| Source                     | Maps to                  |
|----------------------------|--------------------------|
| HTTP 401                   | AuthenticationError      |
| Other HTTP 4xx/5xx         | CommandError             |
| URLError (DNS / refused)   | SZGConnectionError       |
| socket.timeout             | SZGTimeoutError          |
"""

from __future__ import annotations

import io
import json
import socket
import urllib.error
from unittest.mock import patch

import pytest

from pyszg import (
    AuthenticationError,
    CommandError,
    SZGCloudAuth,
    SZGCloudClient,
    SZGConnectionError,
    SZGTimeoutError,
    TokenSet,
)


def _client() -> SZGCloudClient:
    """Return a client with non-expired bogus tokens that won't refresh."""
    tokens = TokenSet(
        id_token="header.eyJzdWIiOiJ4In0=.sig",  # any string; we never decode
        refresh_token="rt",
        user_id="user-1",
        expires_at=2_000_000_000,  # far in the future, no refresh
    )
    auth = SZGCloudAuth()
    return SZGCloudClient(tokens, auth)


def _http_error(status: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://prod.iot.subzero.com/x",
        code=status,
        msg="err",
        hdrs={},
        fp=io.BytesIO(body.encode()),
    )


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_http_401_maps_to_authentication_error(mock_urlopen):
    mock_urlopen.side_effect = _http_error(401, json.dumps({"Message": "unauthorized"}))
    with pytest.raises(AuthenticationError) as ei:
        _client()._request("GET", "/x")
    assert ei.value.status == 401


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_http_403_maps_to_command_error(mock_urlopen):
    mock_urlopen.side_effect = _http_error(403, json.dumps({"Message": "forbidden"}))
    with pytest.raises(CommandError) as ei:
        _client()._request("GET", "/x")
    assert ei.value.status == 403


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_http_500_maps_to_command_error(mock_urlopen):
    mock_urlopen.side_effect = _http_error(500, json.dumps({"Message": "server"}))
    with pytest.raises(CommandError) as ei:
        _client()._request("GET", "/x")
    assert ei.value.status == 500


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_http_500_with_ok_body_is_treated_as_success(mock_urlopen):
    """The CAT module returns 500 with body 'OK' for direct method calls.

    This is a known quirk and must remain a success path.
    """
    mock_urlopen.side_effect = _http_error(500, json.dumps({"Message": "OK"}))
    resp = _client()._request("POST", "/x", data={"k": "v"})
    assert resp == {"_raw": "OK"}


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_url_error_maps_to_connection_error(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
    with pytest.raises(SZGConnectionError):
        _client()._request("GET", "/x")


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_dns_failure_maps_to_connection_error(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.URLError(
        socket.gaierror(-2, "Name or service not known")
    )
    with pytest.raises(SZGConnectionError):
        _client()._request("GET", "/x")


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_socket_timeout_maps_to_timeout_error(mock_urlopen):
    mock_urlopen.side_effect = socket.timeout("timed out")
    with pytest.raises(SZGTimeoutError):
        _client()._request("GET", "/x")


@patch("pyszg.cloud_client.urllib.request.urlopen")
def test_url_error_wrapping_timeout_maps_to_timeout_error(mock_urlopen):
    """Some Python versions wrap socket.timeout in URLError; verify that
    path also reaches SZGTimeoutError."""
    mock_urlopen.side_effect = urllib.error.URLError(socket.timeout("timed out"))
    with pytest.raises(SZGTimeoutError):
        _client()._request("GET", "/x")
