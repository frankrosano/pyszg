"""Tests for cloud_auth: JWT decode, token-response parsing, expiry margin,
and the token-endpoint error mapping.

All network is patched (``pyszg.cloud_auth.urllib.request.urlopen``); these
run fully offline.
"""

from __future__ import annotations

import base64
import io
import json
import socket
import time
import urllib.error
from unittest.mock import patch

import pytest

from pyszg import (
    AuthenticationError,
    SZGCloudAuth,
    SZGConnectionError,
    SZGTimeoutError,
    TokenSet,
)
from pyszg.cloud_auth import _decode_jwt_claims, _token_request


def _make_jwt(claims: dict) -> str:
    """Build an unsigned JWT (header.payload.sig) embedding the given claims."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


# --- _decode_jwt_claims --------------------------------------------------


@pytest.mark.parametrize("filler", range(0, 6))
def test_decode_jwt_claims_roundtrips_every_padding(filler):
    """Varying the payload length by 0..5 chars exercises every length
    residue, including the aligned (len % 4 == 0) case that the old
    ``4 - len % 4`` padding over-padded.
    """
    claims = {"sub": "x" * filler, "exp": 1700000000}
    assert _decode_jwt_claims(_make_jwt(claims)) == claims


def test_decode_jwt_claims_invalid_returns_empty():
    assert _decode_jwt_claims("not-a-jwt") == {}


# --- _parse_token_response ----------------------------------------------


def test_parse_token_response_prefers_exp_claim():
    auth = SZGCloudAuth()
    resp = {
        "id_token": _make_jwt({"sub": "u1", "exp": 9_999_999_999}),
        "refresh_token": "rt",
        "id_token_expires_in": 60,  # should be ignored in favor of exp
    }
    tokens = auth._parse_token_response(resp)
    assert tokens.expires_at == 9_999_999_999.0
    assert tokens.user_id == "u1"
    assert tokens.refresh_token == "rt"


def test_parse_token_response_falls_back_to_expires_in():
    auth = SZGCloudAuth()
    resp = {
        "id_token": _make_jwt({"sub": "u1"}),  # no exp claim
        "id_token_expires_in": "120",          # string, must be coerced
    }
    before = time.time()
    tokens = auth._parse_token_response(resp)
    assert before + 119 <= tokens.expires_at <= time.time() + 121


def test_parse_token_response_missing_id_token_raises():
    with pytest.raises(AuthenticationError):
        SZGCloudAuth()._parse_token_response({"refresh_token": "rt"})


def test_parse_token_response_malformed_id_token_raises():
    # Middle segment is not valid base64/JSON.
    with pytest.raises(AuthenticationError):
        SZGCloudAuth()._parse_token_response({"id_token": "a.@@@@.c"})


# --- TokenSet.is_expired margin -----------------------------------------


def test_is_expired_applies_refresh_margin():
    # Inside the 60s margin -> treated as expired (proactive refresh).
    assert TokenSet(expires_at=time.time() + 30).is_expired is True
    # Comfortably beyond the margin -> still valid.
    assert TokenSet(expires_at=time.time() + 120).is_expired is False


# --- _token_request error mapping ---------------------------------------


@patch("pyszg.cloud_auth.urllib.request.urlopen")
def test_token_request_http_error_maps_to_auth_error(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.HTTPError(
        url="https://token", code=400, msg="bad", hdrs={},
        fp=io.BytesIO(b'{"error":"invalid_grant"}'),
    )
    with pytest.raises(AuthenticationError) as ei:
        _token_request({"grant_type": "refresh_token"})
    assert ei.value.status == 400


@patch("pyszg.cloud_auth.urllib.request.urlopen")
def test_token_request_timeout_maps_to_timeout_error(mock_urlopen):
    mock_urlopen.side_effect = socket.timeout("timed out")
    with pytest.raises(SZGTimeoutError):
        _token_request({"grant_type": "refresh_token"})


@patch("pyszg.cloud_auth.urllib.request.urlopen")
def test_token_request_urlerror_maps_to_connection_error(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
    with pytest.raises(SZGConnectionError):
        _token_request({"grant_type": "refresh_token"})
