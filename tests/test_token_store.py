"""Tests for the TokenStore — refresh persistence and shared-state semantics.

These guard against the regression that caused authentication to silently
expire after a process restart: the cloud client refreshed in memory but
the rotated refresh_token was never persisted, so the next start tried
to refresh with a token Azure had already invalidated.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from pyszg import (
    AuthenticationError,
    SZGCloudAuth,
    SZGCloudClient,
    TokenSet,
    TokenStore,
)


def _expired_tokens() -> TokenSet:
    return TokenSet(
        id_token="header.eyJzdWIiOiJ4In0=.sig",
        refresh_token="rt-original",
        user_id="user-1",
        expires_at=time.time() - 60,
    )


def _fresh_tokens() -> TokenSet:
    return TokenSet(
        id_token="header.eyJzdWIiOiJ4In0=.sig",
        refresh_token="rt-original",
        user_id="user-1",
        expires_at=time.time() + 3600,
    )


def _rotated_tokens(label: str = "rotated") -> TokenSet:
    return TokenSet(
        id_token=f"new-id-{label}",
        refresh_token=f"rt-{label}",
        user_id="user-1",
        expires_at=time.time() + 3600,
    )


def test_get_valid_returns_current_when_not_expired():
    auth = MagicMock(spec=SZGCloudAuth)
    tokens = _fresh_tokens()
    store = TokenStore(tokens, auth)

    assert store.get_valid() is tokens
    auth.refresh.assert_not_called()


def test_get_valid_refreshes_when_expired():
    auth = MagicMock(spec=SZGCloudAuth)
    rotated = _rotated_tokens()
    auth.refresh.return_value = rotated

    store = TokenStore(_expired_tokens(), auth)
    result = store.get_valid()

    assert result is rotated
    assert store.tokens is rotated
    auth.refresh.assert_called_once()


def test_on_refresh_callback_fires_with_rotated_tokens():
    """The persistence hook must see the rotated tokens — that's the whole
    point of this class. Without it, B2C invalidates the original refresh
    token on first rotation and the next process start fails to refresh.
    """
    auth = MagicMock(spec=SZGCloudAuth)
    rotated = _rotated_tokens()
    auth.refresh.return_value = rotated

    seen: list[TokenSet] = []
    store = TokenStore(_expired_tokens(), auth, on_refresh=seen.append)
    store.get_valid()

    assert seen == [rotated]


def test_on_refresh_not_called_when_tokens_still_valid():
    auth = MagicMock(spec=SZGCloudAuth)
    seen: list[TokenSet] = []
    store = TokenStore(_fresh_tokens(), auth, on_refresh=seen.append)
    store.get_valid()
    assert seen == []


def test_on_refresh_exception_is_swallowed():
    """If the persistence hook raises, the in-memory tokens are still
    usable for the running process. We log and continue rather than
    bubble the persistence failure into every API call.
    """
    auth = MagicMock(spec=SZGCloudAuth)
    rotated = _rotated_tokens()
    auth.refresh.return_value = rotated

    def boom(_tokens):
        raise RuntimeError("disk full")

    store = TokenStore(_expired_tokens(), auth, on_refresh=boom)
    result = store.get_valid()  # should not raise

    assert result is rotated
    assert store.tokens is rotated


def test_refresh_failure_propagates():
    auth = MagicMock(spec=SZGCloudAuth)
    auth.refresh.side_effect = AuthenticationError("invalid_grant")

    store = TokenStore(_expired_tokens(), auth)
    with pytest.raises(AuthenticationError):
        store.get_valid()


def test_concurrent_get_valid_serializes_refresh():
    """Two threads hitting an expired store must share one refresh, not
    each issue a refresh against the same already-rotated refresh_token
    (the second of which would 401 since B2C invalidates on first use).
    """
    import threading

    auth = MagicMock(spec=SZGCloudAuth)
    rotated = _rotated_tokens()
    refresh_calls = 0

    def slow_refresh(_tokens):
        nonlocal refresh_calls
        refresh_calls += 1
        time.sleep(0.05)
        return rotated

    auth.refresh.side_effect = slow_refresh

    store = TokenStore(_expired_tokens(), auth)

    results: list[TokenSet] = []
    def worker():
        results.append(store.get_valid())

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert refresh_calls == 1
    assert all(r is rotated for r in results)


# --- SZGCloudClient + SZGCloudSignalR share one TokenStore ---------------


def test_client_uses_supplied_tokenstore():
    auth = MagicMock(spec=SZGCloudAuth)
    store = TokenStore(_fresh_tokens(), auth)
    client = SZGCloudClient(store)

    assert client.token_store is store


def test_signalr_shares_tokenstore_with_client():
    """The whole point of the shared store: SignalR sees the rotated
    refresh_token the moment the cloud client rotates it (or vice
    versa), so neither tries to refresh with a stale token in-process.
    """
    pytest.importorskip("websockets")
    from pyszg import SZGCloudSignalR

    auth = MagicMock(spec=SZGCloudAuth)
    store = TokenStore(_fresh_tokens(), auth)

    client = SZGCloudClient(store)
    signalr = SZGCloudSignalR(store)

    assert client.token_store is signalr.token_store


# --- force_refresh ------------------------------------------------------


def test_force_refresh_refreshes_even_when_not_expired():
    auth = MagicMock(spec=SZGCloudAuth)
    rotated = _rotated_tokens()
    auth.refresh.return_value = rotated

    store = TokenStore(_fresh_tokens(), auth)  # not expired
    result = store.force_refresh()

    assert result is rotated
    auth.refresh.assert_called_once()


def test_force_refresh_skips_when_already_rotated_past_stale():
    """If the store already advanced past the token the caller used, a
    second concurrent 401 must not trigger a redundant refresh (which would
    rotate again and fire on_refresh again)."""
    auth = MagicMock(spec=SZGCloudAuth)
    current = _fresh_tokens()
    store = TokenStore(current, auth)

    some_other_stale = _expired_tokens()  # not the store's current object
    result = store.force_refresh(stale=some_other_stale)

    assert result is current
    auth.refresh.assert_not_called()


def test_force_refresh_refreshes_when_stale_matches_current():
    auth = MagicMock(spec=SZGCloudAuth)
    rotated = _rotated_tokens()
    auth.refresh.return_value = rotated

    current = _fresh_tokens()
    store = TokenStore(current, auth)

    result = store.force_refresh(stale=current)  # caller saw the current token

    assert result is rotated
    auth.refresh.assert_called_once()
