"""Tests for SZGCloudAuth.login_with_password (headless B2C sign-in).

Fully offline. Every B2C page/XHR request goes through a scripted fake opener
(injected by patching ``pyszg.cloud_auth._build_opener``), and the token
endpoint is a patched ``urlopen``. An autouse guard makes any un-mocked
``urlopen`` / ``build_opener`` / socket connect raise, so an accidental real
request fails the test instead of reaching login.subzero-wolf.com.

All values here are synthetic (``user@example.com``, ``FAKE-CODE``, ...).
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import http.cookiejar
import io
import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from pyszg import (
    AuthenticationError,
    InteractiveLoginRequired,
    SZGCloudAuth,
    SZGConnectionError,
    SZGError,
    SZGTimeoutError,
    TokenSet,
)
from pyszg.cloud_auth import (
    _TOKEN_ATTEMPTS,
    _TOKEN_RETRY_DELAY,
    _NoRedirectHandler,
    _b2c_request,
    _build_opener,
    _extract_code,
    _parse_settings,
    _pkce_pair,
    _settings_fields,
)
from pyszg.cloud_const import AUTHORIZE_URL, B2C_HOST, REDIRECT_URI

# Captured before the guard replaces it (building an opener is not network).
_REAL_BUILD_OPENER = urllib.request.build_opener

EMAIL = "user@example.com"
PASSWORD = "S3cr3t-Pa55!"
CSRF = "FAKE/CSRF+x=="
TRANS_ID = "StateProperties=FAKE TRANS+ID"
TENANT = "/SubZeroB2CPrd.onmicrosoft.com/B2C_1A_signup_signin"
POLICY = "B2C_1A_signup_signin"
API = "CombinedSigninAndSignup"

EXPECTED_SELECT_URL = (
    f"https://{B2C_HOST}{TENANT}/SelfAsserted"
    f"?tx=StateProperties=FAKE%20TRANS%2BID&p={POLICY}"
)
EXPECTED_CONFIRM_URL = (
    f"https://{B2C_HOST}{TENANT}/api/{API}/confirmed"
    f"?rememberMe=false&csrf_token=FAKE%2FCSRF%2Bx%3D%3D"
    f"&tx=StateProperties=FAKE%20TRANS%2BID&p={POLICY}"
)


# --- fixtures / fakes ---------------------------------------------------


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail loudly if anything tries to touch the network."""

    def _blocked(*args, **kwargs):
        raise AssertionError("network disabled")

    monkeypatch.setattr(urllib.request, "urlopen", _blocked)
    monkeypatch.setattr(urllib.request, "build_opener", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked)


@pytest.fixture
def sleeps(monkeypatch):
    """Record (and skip) retry delays."""
    calls: list[float] = []
    monkeypatch.setattr("pyszg.cloud_auth.time.sleep", calls.append)
    return calls


def _make_jwt(claims: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


class _Resp:
    def __init__(self, status: int, headers: dict[str, str], body: str) -> None:
        self.status = status
        self.headers = headers
        self._body = body.encode()

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]


class FakeOpener:
    """Scripted stand-in for the no-redirect opener.

    Each script item is ``(status, headers, body)``, an exception instance to
    raise, or a callable ``f(fake) -> (status, headers, body)``. Like the real
    opener, non-2xx statuses are raised as ``HTTPError``.
    """

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.requests: list[urllib.request.Request] = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item = item(self)
        status, headers, body = item
        if status >= 300:
            msg = http.client.HTTPMessage()
            for k, v in headers.items():
                msg[k] = v
            raise urllib.error.HTTPError(
                req.full_url, status, "msg", msg, io.BytesIO(body.encode())
            )
        return _Resp(status, headers, body)


def _settings_page(**overrides) -> str:
    settings = {
        "csrf": CSRF,
        "transId": TRANS_ID,
        "hosts": {"tenant": TENANT, "policy": POLICY},
        "api": API,
        # Nested object + a "};" inside a string prove raw_decode is used
        # rather than a regex up to the first "};".
        "config": {"a": {"b": 1}, "note": "has }; inside"},
    }
    settings.update(overrides)
    return f"<html><script>var SETTINGS = {json.dumps(settings)};\nvar x = 1;</script></html>"


def _authorize_ok():
    return (200, {}, _settings_page())


def _selfasserted_ok():
    return (200, {"Content-Type": "application/json"}, '{"status":"200"}')


def _state_of(fake: FakeOpener) -> str:
    query = urllib.parse.urlsplit(fake.requests[0].full_url).query
    return urllib.parse.parse_qs(query)["state"][0]


def _redirect(template: str = "{base}/?state={state}&code=FAKE-CODE", base: str = REDIRECT_URI):
    def _f(fake: FakeOpener):
        loc = template.format(base=base, state=_state_of(fake))
        return (302, {"Location": loc}, "")
    return _f


def _happy_script() -> list:
    return [_authorize_ok(), _selfasserted_ok(), _redirect()]


def _token_response(**claims) -> bytes:
    claims = {"sub": "user-1", "exp": time.time() + 3600, "email": EMAIL, **claims}
    return json.dumps({
        "id_token": _make_jwt(claims),
        "refresh_token": "FAKE-REFRESH",
        "id_token_expires_in": 3600,
    }).encode()


def _urlopen_resp(body: bytes) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = body
    return resp


def _http_error(code: int, body: bytes = b"<html>err</html>") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://token", code=code, msg="x", hdrs={}, fp=io.BytesIO(body)
    )


def _install_opener(monkeypatch, script: list) -> FakeOpener:
    fake = FakeOpener(script)
    monkeypatch.setattr(
        "pyszg.cloud_auth._build_opener",
        lambda: (fake, http.cookiejar.CookieJar()),
    )
    return fake


def _headers_of(req: urllib.request.Request) -> dict[str, str]:
    return {k.lower(): v for k, v in req.header_items()}


def _chain_text(exc: BaseException | None) -> str:
    parts: list[str] = []
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        parts += [str(exc), repr(exc)]
        exc = exc.__cause__ or exc.__context__
    return "\n".join(parts)


def _login(monkeypatch, script: list, token_side_effect=None):
    """Run login_with_password against a scripted opener.

    Returns ``(fake_opener, urlopen_mock)``; raises whatever login raises.
    """
    fake = _install_opener(monkeypatch, script)
    with patch("pyszg.cloud_auth.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = token_side_effect or [
            _urlopen_resp(_token_response())
        ]
        try:
            tokens = SZGCloudAuth().login_with_password(EMAIL, PASSWORD)
        except BaseException as exc:
            exc.fake_opener = fake  # type: ignore[attr-defined]
            exc.urlopen_mock = mock_urlopen  # type: ignore[attr-defined]
            raise
    return tokens, fake, mock_urlopen


# --- success -------------------------------------------------------------


def test_login_success_end_to_end(monkeypatch):
    tokens, fake, mock_urlopen = _login(monkeypatch, _happy_script())

    assert isinstance(tokens, TokenSet)
    assert tokens.refresh_token == "FAKE-REFRESH"
    assert tokens.user_id == "user-1"
    assert tokens.email == EMAIL
    assert fake.script == []
    assert len(fake.requests) == 3

    # 1. authorize
    authorize, select, confirm = fake.requests
    assert authorize.get_method() == "GET"
    assert authorize.full_url.startswith(AUTHORIZE_URL + "?")
    assert "user-agent" in _headers_of(authorize)

    # 2. SelfAsserted POST
    assert select.get_method() == "POST"
    assert select.full_url == EXPECTED_SELECT_URL
    h = _headers_of(select)
    assert h["x-csrf-token"] == CSRF
    assert h["x-requested-with"] == "XMLHttpRequest"
    assert h["content-type"] == "application/x-www-form-urlencoded; charset=UTF-8"
    assert h["accept"] == "application/json, text/javascript, */*; q=0.01"
    assert h["origin"] == f"https://{B2C_HOST}"
    assert h["referer"] == authorize.full_url
    body = urllib.parse.parse_qs(select.data.decode())
    assert body == {
        "request_type": ["RESPONSE"],
        "signInName": [EMAIL],
        "password": [PASSWORD],
    }

    # 3. confirmed GET
    assert confirm.get_method() == "GET"
    assert confirm.full_url == EXPECTED_CONFIRM_URL
    assert _headers_of(confirm)["referer"] == authorize.full_url

    # 4. token exchange used the same PKCE pair and the extracted code
    assert mock_urlopen.call_count == 1
    token_req = mock_urlopen.call_args.args[0]
    form = urllib.parse.parse_qs(token_req.data.decode())
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == ["FAKE-CODE"]
    assert form["redirect_uri"] == [REDIRECT_URI]
    challenge = urllib.parse.parse_qs(
        urllib.parse.urlsplit(authorize.full_url).query
    )["code_challenge"][0]
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(form["code_verifier"][0].encode()).digest()
    ).rstrip(b"=").decode()
    assert challenge == expected


def test_login_success_without_trailing_slash_and_code_first(monkeypatch):
    script = [
        _authorize_ok(),
        _selfasserted_ok(),
        _redirect("{base}?code=FAKE-CODE&state={state}"),
    ]
    tokens, _fake, _ = _login(monkeypatch, script)
    assert tokens.refresh_token == "FAKE-REFRESH"


def test_login_strips_email_whitespace(monkeypatch):
    fake = _install_opener(monkeypatch, _happy_script())
    with patch("pyszg.cloud_auth.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [_urlopen_resp(_token_response())]
        SZGCloudAuth().login_with_password(f"  {EMAIL}\n", PASSWORD)
    body = urllib.parse.parse_qs(fake.requests[1].data.decode())
    assert body["signInName"] == [EMAIL]


# --- wrong credentials ---------------------------------------------------


def test_login_wrong_password_http_400(monkeypatch):
    script = [
        _authorize_ok(),
        (400, {"Content-Type": "application/json"},
         '{"status":"400","message":"SERVER-TEXT password incorrect"}'),
    ]
    with pytest.raises(AuthenticationError) as ei:
        _login(monkeypatch, script)
    assert not isinstance(ei.value, InteractiveLoginRequired)
    assert ei.value.status == 400
    assert "SERVER-TEXT" not in _chain_text(ei.value)
    # No further requests (confirm / token exchange) after a rejection.
    assert len(ei.value.fake_opener.requests) == 2
    assert ei.value.urlopen_mock.call_count == 0


def test_login_wrong_password_json_status_400_with_http_200(monkeypatch):
    script = [
        _authorize_ok(),
        (200, {}, '{"status":"400","message":"SERVER-TEXT"}'),
    ]
    with pytest.raises(AuthenticationError) as ei:
        _login(monkeypatch, script)
    assert not isinstance(ei.value, InteractiveLoginRequired)
    assert "SERVER-TEXT" not in _chain_text(ei.value)


@pytest.mark.parametrize("second", [
    (200, {}, "<html>not json</html>"),
    (200, {}, '{"status":"500"}'),
    (403, {}, "forbidden"),
    (404, {}, "nope"),
])
def test_login_unexpected_selfasserted_is_interactive(monkeypatch, second):
    with pytest.raises(InteractiveLoginRequired):
        _login(monkeypatch, [_authorize_ok(), second])


# --- authorize / SETTINGS ------------------------------------------------


def test_login_authorize_not_200_is_interactive(monkeypatch):
    with pytest.raises(InteractiveLoginRequired):
        _login(monkeypatch, [(302, {"Location": "https://x.example/"}, "")])


@pytest.mark.parametrize("page", [
    "<html>no settings here</html>",
    "<script>var SETTINGS = {not json at all};</script>",
    "<script>var SETTINGS = [1, 2];</script>",
    _settings_page(csrf=""),
    _settings_page(transId=None),
    _settings_page(api=7),
    _settings_page(hosts={"tenant": TENANT}),
    _settings_page(hosts="nope"),
    _settings_page(hosts={"tenant": "/t?x=1", "policy": POLICY}),
    _settings_page(hosts={"tenant": "//evil.example/x", "policy": POLICY}),
    _settings_page(api="a/../b"),
    _settings_page(hosts={"tenant": TENANT, "policy": "p&q=1"}),
])
def test_login_bad_settings_is_interactive(monkeypatch, page):
    with pytest.raises(InteractiveLoginRequired) as ei:
        _login(monkeypatch, [(200, {}, page)])
    # Stopped before sending any credentials.
    assert len(ei.value.fake_opener.requests) == 1


# --- confirmed -----------------------------------------------------------


def test_login_confirmed_html_is_interactive(monkeypatch):
    script = [_authorize_ok(), _selfasserted_ok(), (200, {}, "<html>MFA</html>")]
    with pytest.raises(InteractiveLoginRequired) as ei:
        _login(monkeypatch, script)
    assert ei.value.urlopen_mock.call_count == 0


@pytest.mark.parametrize("redirect", [
    _redirect(base="https://evil.example/cb"),
    _redirect("{base}/?state={state}&error=access_denied"),
    _redirect("{base}/?state={state}"),
    _redirect(base="other.scheme://auth"),
    lambda fake: (302, {}, ""),  # redirect without Location
    lambda fake: (404, {}, "nope"),
])
def test_login_confirmed_without_code_is_interactive(monkeypatch, redirect):
    with pytest.raises(InteractiveLoginRequired) as ei:
        _login(monkeypatch, [_authorize_ok(), _selfasserted_ok(), redirect])
    assert ei.value.urlopen_mock.call_count == 0


def test_login_state_mismatch_is_plain_szgerror(monkeypatch):
    script = [
        _authorize_ok(),
        _selfasserted_ok(),
        lambda fake: (
            302,
            {"Location": f"{REDIRECT_URI}/?state=WRONG&code=FAKE-CODE"},
            "",
        ),
    ]
    with pytest.raises(SZGError) as ei:
        _login(monkeypatch, script)
    assert type(ei.value) is SZGError
    assert ei.value.urlopen_mock.call_count == 0


# --- transport errors ----------------------------------------------------


def test_login_authorize_5xx_is_connection_error(monkeypatch):
    with pytest.raises(SZGConnectionError) as ei:
        _login(monkeypatch, [(503, {}, "<html>down</html>")])
    assert ei.value.status == 503
    assert "<html" not in str(ei.value)


def test_login_selfasserted_5xx_and_429_are_connection_errors(monkeypatch):
    for status in (500, 429):
        with pytest.raises(SZGConnectionError) as ei:
            _login(monkeypatch, [_authorize_ok(), (status, {}, "x")])
        assert ei.value.status == status


def test_login_urlerror_is_connection_error(monkeypatch):
    script = [_authorize_ok(), urllib.error.URLError("Connection refused")]
    with pytest.raises(SZGConnectionError):
        _login(monkeypatch, script)


def test_login_oserror_is_connection_error(monkeypatch):
    with pytest.raises(SZGConnectionError):
        _login(monkeypatch, [ConnectionResetError("reset")])


def test_login_socket_timeout_is_timeout_error(monkeypatch):
    with pytest.raises(SZGTimeoutError):
        _login(monkeypatch, [_authorize_ok(), socket.timeout("timed out")])


def test_login_urlerror_timeout_is_timeout_error(monkeypatch):
    err = urllib.error.URLError(socket.timeout("timed out"))
    with pytest.raises(SZGTimeoutError):
        _login(monkeypatch, [err])


# --- token exchange retry ------------------------------------------------


def test_login_token_504_then_success_retries(monkeypatch, sleeps):
    effects = [_http_error(504), _urlopen_resp(_token_response())]
    tokens, _fake, mock_urlopen = _login(monkeypatch, _happy_script(), effects)
    assert tokens.refresh_token == "FAKE-REFRESH"
    assert mock_urlopen.call_count == 2
    assert sleeps == [_TOKEN_RETRY_DELAY]
    # The retry re-sends the same authorization code.
    bodies = [
        urllib.parse.parse_qs(c.args[0].data.decode())
        for c in mock_urlopen.call_args_list
    ]
    assert bodies[0] == bodies[1]


def test_login_token_timeout_then_success_retries(monkeypatch, sleeps):
    effects = [socket.timeout("t"), urllib.error.URLError("x"),
               _urlopen_resp(_token_response())]
    tokens, _fake, mock_urlopen = _login(monkeypatch, _happy_script(), effects)
    assert tokens.refresh_token == "FAKE-REFRESH"
    assert mock_urlopen.call_count == 3
    assert len(sleeps) == 2


def test_login_token_exhausts_retries(monkeypatch, sleeps):
    effects = [_http_error(504)] * _TOKEN_ATTEMPTS
    with pytest.raises(SZGConnectionError) as ei:
        _login(monkeypatch, _happy_script(), effects)
    assert ei.value.status == 504
    assert ei.value.urlopen_mock.call_count == _TOKEN_ATTEMPTS
    assert len(sleeps) == _TOKEN_ATTEMPTS - 1


def test_login_token_400_is_not_retried(monkeypatch, sleeps):
    effects = [_http_error(400, b'{"error":"invalid_grant"}')]
    with pytest.raises(AuthenticationError) as ei:
        _login(monkeypatch, _happy_script(), effects)
    assert not isinstance(ei.value, InteractiveLoginRequired)
    assert ei.value.status == 400
    assert ei.value.urlopen_mock.call_count == 1
    assert sleeps == []


# --- input validation ----------------------------------------------------


@pytest.mark.parametrize("email,password", [
    ("", PASSWORD),
    ("   ", PASSWORD),
    (EMAIL, ""),
    ("", ""),
])
def test_login_empty_inputs_raise_without_network(email, password):
    # The autouse guard turns any network attempt into AssertionError.
    with pytest.raises(AuthenticationError) as ei:
        SZGCloudAuth().login_with_password(email, password)
    assert not isinstance(ei.value, InteractiveLoginRequired)


# --- password never leaks ------------------------------------------------


def _leak_scenarios() -> list:
    return [
        ("wrong_password_400", [_authorize_ok(), (400, {}, '{"status":"400"}')], None),
        ("wrong_password_json", [_authorize_ok(), (200, {}, '{"status":"400"}')], None),
        ("interactive_confirmed",
         [_authorize_ok(), _selfasserted_ok(), (200, {}, "<html>MFA</html>")], None),
        ("post_urlerror", [_authorize_ok(), urllib.error.URLError("refused")], None),
        ("post_timeout", [_authorize_ok(), socket.timeout("t")], None),
        ("post_5xx", [_authorize_ok(), (500, {}, "x")], None),
        ("state_mismatch",
         [_authorize_ok(), _selfasserted_ok(),
          lambda f: (302, {"Location": f"{REDIRECT_URI}/?state=BAD&code=C"}, "")],
         None),
        ("token_504", _happy_script(), [_http_error(504)] * _TOKEN_ATTEMPTS),
        ("token_400", _happy_script(), [_http_error(400, b'{"error":"invalid_grant"}')]),
    ]


@pytest.mark.parametrize(
    "script,effects",
    [pytest.param(s, e, id=name) for name, s, e in _leak_scenarios()],
)
def test_password_never_in_exceptions_logs_or_urls(
    monkeypatch, caplog, sleeps, script, effects
):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(SZGError) as ei:
        _login(monkeypatch, script, effects)

    encoded = urllib.parse.quote_plus(PASSWORD)
    text = _chain_text(ei.value)
    assert PASSWORD not in text and encoded not in text
    assert PASSWORD not in caplog.text and encoded not in caplog.text

    for req in ei.value.fake_opener.requests:
        assert PASSWORD not in req.full_url and encoded not in req.full_url
        for value in _headers_of(req).values():
            assert PASSWORD not in value and encoded not in value
    for call in ei.value.urlopen_mock.call_args_list:
        token_req = call.args[0]
        assert PASSWORD not in token_req.full_url
        assert PASSWORD not in token_req.data.decode()
        assert encoded not in token_req.data.decode()


def test_password_only_in_post_body_on_success(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    _tokens, fake, mock_urlopen = _login(monkeypatch, _happy_script())
    encoded = urllib.parse.quote_plus(PASSWORD)
    for i, req in enumerate(fake.requests):
        data = req.data.decode() if req.data else ""
        assert (encoded in data) == (i == 1)
        assert PASSWORD not in req.full_url
    assert PASSWORD not in caplog.text and encoded not in caplog.text
    assert PASSWORD not in mock_urlopen.call_args.args[0].data.decode()


# --- helper unit tests ---------------------------------------------------


def test_parse_settings_handles_nested_and_brace_semicolon_in_string():
    page = _settings_page()
    settings = _parse_settings(page)
    assert settings is not None
    assert settings["csrf"] == CSRF
    assert settings["config"] == {"a": {"b": 1}, "note": "has }; inside"}


@pytest.mark.parametrize("page", [
    "",
    "<html>nothing</html>",
    "var SETTINGS = {broken",
    "var SETTINGS = [1];",
    "var SETTINGS = 5;",
])
def test_parse_settings_returns_none_when_unusable(page):
    assert _parse_settings(page) is None


def test_settings_fields_returns_values():
    settings = json.loads(
        _settings_page().split("var SETTINGS = ", 1)[1].split(";\nvar x", 1)[0]
    )
    assert _settings_fields(settings) == (CSRF, TRANS_ID, TENANT, POLICY, API)


@pytest.mark.parametrize("location", [
    f"{REDIRECT_URI}/?state=ST&code=FAKE-CODE",   # trailing slash, state first
    f"{REDIRECT_URI}?state=ST&code=FAKE-CODE",    # no trailing slash
    f"{REDIRECT_URI}/?code=FAKE-CODE&state=ST",   # code first
    f"{REDIRECT_URI}/?code=FAKE-CODE&state=ST&extra=1",
])
def test_extract_code_success(location):
    assert _extract_code(location, "ST") == "FAKE-CODE"


@pytest.mark.parametrize("location", [
    f"{REDIRECT_URI}/?state=ST",                                   # no code
    f"{REDIRECT_URI}/?state=ST&code=",                             # empty code
    f"{REDIRECT_URI}/?state=ST&error=access_denied",               # error
    f"{REDIRECT_URI}/?state=ST&code=FAKE-CODE&error=server_error",  # error wins
    "https://evil.example/auth/?state=ST&code=FAKE-CODE",           # wrong scheme/host
    "other.scheme://auth/?state=ST&code=FAKE-CODE",                 # wrong scheme
    f"{REDIRECT_URI}/extra/?state=ST&code=FAKE-CODE",               # wrong path
    "",
])
def test_extract_code_interactive_cases(location):
    with pytest.raises(InteractiveLoginRequired):
        _extract_code(location, "ST")


@pytest.mark.parametrize("state_part", ["state=OTHER", "state=", ""])
def test_extract_code_state_mismatch(state_part):
    query = "&".join(p for p in (state_part, "code=FAKE-CODE") if p)
    with pytest.raises(SZGError) as ei:
        _extract_code(f"{REDIRECT_URI}/?{query}", "ST")
    assert type(ei.value) is SZGError


def test_extract_code_non_ascii_state_does_not_crash():
    with pytest.raises(SZGError):
        _extract_code(f"{REDIRECT_URI}/?state=%C3%A9&code=FAKE-CODE", "ST")


def test_no_redirect_handler_declines_redirects():
    handler = _NoRedirectHandler()
    result = handler.redirect_request(None, None, 302, "Found", {}, "https://x/")
    assert result is None


def test_build_opener_has_cookie_jar_and_no_redirect(monkeypatch):
    monkeypatch.setattr(urllib.request, "build_opener", _REAL_BUILD_OPENER)
    opener, jar = _build_opener()
    assert isinstance(jar, http.cookiejar.CookieJar)
    assert any(
        isinstance(h, urllib.request.HTTPCookieProcessor) and h.cookiejar is jar
        for h in opener.handlers
    )
    assert any(isinstance(h, _NoRedirectHandler) for h in opener.handlers)
    assert not any(type(h) is urllib.request.HTTPRedirectHandler for h in opener.handlers)


def test_pkce_pair_is_valid_s256():
    verifier, challenge = _pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    assert challenge == expected
    assert _pkce_pair()[0] != verifier


def test_b2c_request_returns_lowercased_headers_for_redirect():
    fake = FakeOpener([(302, {"Location": "x://y/?code=1"}, "")])
    status, headers, body = _b2c_request(fake, "GET", "https://example.test/a?tx=SECRET-TX")
    assert status == 302
    assert headers["location"] == "x://y/?code=1"
    assert body == ""


def test_b2c_request_error_messages_exclude_url():
    url = "https://example.test/a?tx=SECRET-TX&csrf_token=SECRET-CSRF"
    for item in (
        (503, {}, "x"),
        urllib.error.URLError("refused"),
        socket.timeout("t"),
        ConnectionResetError("reset"),
    ):
        with pytest.raises(SZGError) as ei:
            _b2c_request(FakeOpener([item]), "GET", url)
        assert "SECRET" not in _chain_text(ei.value)
