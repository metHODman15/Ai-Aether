"""Tests for backend.salesforce_client session/idle behavior.

The real ``simple_salesforce.Salesforce`` is heavyweight and makes
network calls in ``__init__``; we substitute a lightweight stand-in via
the ``_SF_FACTORY`` seam.  These tests cover:

* ``warm_up`` flips ``is_online`` and triggers the status callback.
* Idle-timeout forces a re-login on the next query.
* Offline → online transitions broadcast exactly one status change.

The new OAuth-based interface uses a TokenStore; we supply a
lightweight in-memory stub so no real database is touched.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from backend import salesforce_client as sf_mod
from backend.salesforce_client import SalesforceClient


# ── Lightweight TokenStore stub ──────────────────────────────────────────────

class _MemoryTokenStore:
    """In-memory stand-in for TokenStore (no DB, no encryption)."""

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._tokens: dict[str, Any] | None = initial

    def load(self) -> dict[str, Any] | None:
        return self._tokens

    def save(self, tokens: dict[str, Any]) -> None:
        self._tokens = tokens

    def clear(self) -> None:
        self._tokens = None

    def has_tokens(self) -> bool:
        return self._tokens is not None


_FAKE_TOKENS = {
    "access_token": "fake_access_token",
    "refresh_token": "fake_refresh_token",
    "instance_url": "https://test.salesforce.com",
    "issued_at": time.time(),
}


# ── Lightweight Salesforce stub ───────────────────────────────────────────────

class _FakeSF:
    """Minimal stand-in for simple_salesforce.Salesforce."""

    instances: list["_FakeSF"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeSF.instances.append(self)
        # Mimic the describe() shape used to populate stage cache.
        self.Opportunity = type("O", (), {
            "describe": staticmethod(lambda: {
                "fields": [
                    {"name": "StageName", "picklistValues": [
                        {"value": "Qualification", "active": True},
                        {"value": "Proposal", "active": True},
                        {"value": "Closed Won", "active": True},
                    ]},
                ],
            }),
        })()

    def query(self, soql):
        return {"totalSize": 0, "records": []}


@pytest.fixture(autouse=True)
def _reset_fake_sf(monkeypatch):
    _FakeSF.instances = []
    monkeypatch.setattr(sf_mod, "Salesforce", _FakeSF)
    yield


def _new_client(
    idle_seconds: int = 1800,
    callback=None,
    tokens: dict[str, Any] | None = None,
) -> SalesforceClient:
    store = _MemoryTokenStore(tokens if tokens is not None else dict(_FAKE_TOKENS))
    return SalesforceClient(
        token_store=store,
        sf_client_id="test_client_id",
        sf_client_secret="test_client_secret",
        sf_login_url="https://login.salesforce.com",
        idle_refresh_seconds=idle_seconds,
        on_status_change=callback,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_warm_up_flips_online_and_invokes_callback():
    events: list[tuple[bool, str | None]] = []

    async def cb(online: bool, reason: str | None) -> None:
        events.append((online, reason))

    client = _new_client(callback=cb)
    assert client.is_online is False

    await client.warm_up()

    assert client.is_online is True
    assert events == [(True, None)] or events[-1][0] is True
    # Stage cache should be populated from describe()'s picklist.
    assert "Proposal" in client.get_stage_names()


@pytest.mark.asyncio
async def test_no_tokens_fires_auth_required():
    """warm_up with no stored tokens must fire on_auth_required, not crash."""
    auth_required_events: list[int] = []

    async def on_auth_req() -> None:
        auth_required_events.append(1)

    store = _MemoryTokenStore(None)
    client = SalesforceClient(
        token_store=store,
        sf_client_id="cid",
        sf_client_secret="csec",
        on_auth_required=on_auth_req,
    )
    await client.warm_up()

    assert client.is_online is False
    assert len(auth_required_events) >= 1


@pytest.mark.asyncio
async def test_idle_timeout_forces_reconnect(monkeypatch):
    """After the idle window passes, the next query must rebuild the
    Salesforce session rather than reusing the stale one.

    In the OAuth flow the idle-timeout path calls refresh_access_token
    (httpx) before rebuilding the simple_salesforce session. We mock the
    refresh so the test stays offline.
    """
    import backend.salesforce_client as sf_mod2

    refreshed_tokens = {
        **_FAKE_TOKENS,
        "access_token": "REFRESHED_TOKEN",
        "issued_at": time.time(),
    }

    def _fake_refresh(client_id, client_secret, refresh_token, login_url):
        return refreshed_tokens

    monkeypatch.setattr(sf_mod2, "refresh_access_token", _fake_refresh)

    client = _new_client(idle_seconds=1)  # 1-second idle window
    await client.warm_up()
    instances_after_warmup = len(_FakeSF.instances)
    assert instances_after_warmup >= 1

    # Pretend the last activity was well in the past.
    client._last_activity = time.monotonic() - 5.0

    # Any operation that touches the SF client should refresh.
    await client.query_for_entities({"customer_name": "Acme"})

    assert len(_FakeSF.instances) > instances_after_warmup, (
        "Idle timeout should have triggered a fresh Salesforce() session"
    )


@pytest.mark.asyncio
async def test_status_callback_only_fires_on_transition():
    """Repeated successful operations must not spam crm_online events."""
    events: list[bool] = []

    async def cb(online: bool, reason: str | None) -> None:
        events.append(online)

    client = _new_client(callback=cb)
    await client.warm_up()
    await client.query_for_entities({"customer_name": "Acme"})
    await client.query_for_entities({"customer_name": "Globex"})

    # Exactly one offline→online transition expected.
    assert events.count(True) == 1
    assert False not in events


@pytest.mark.asyncio
async def test_recovery_from_query_failure_emits_crm_online():
    """After a transient query failure marks the client offline, the
    very next successful query must flip it back to online and fire
    the callback again — the UI must un-grey without waiting for an
    idle-timeout-driven reconnect."""
    from simple_salesforce.exceptions import SalesforceError

    events: list[bool] = []

    async def cb(online: bool, reason: str | None) -> None:
        events.append(online)

    # Use a fake whose query() can be flipped to raise on demand.
    class _FlakyFakeSF(_FakeSF):
        raise_next: bool = False

        def query(self, soql):
            if _FlakyFakeSF.raise_next:
                _FlakyFakeSF.raise_next = False
                raise SalesforceError("https://x", 500, "Account", "boom")
            return {"totalSize": 0, "records": []}

    import backend.salesforce_client as sf_mod
    sf_mod.Salesforce = _FlakyFakeSF
    _FlakyFakeSF.instances = []

    client = _new_client(callback=cb)
    await client.warm_up()
    assert client.is_online is True
    assert events == [True]

    # Trigger a query failure → offline transition.
    _FlakyFakeSF.raise_next = True
    await client.query_for_entities({"customer_name": "Acme"})
    assert client.is_online is False
    assert events == [True, False]

    # Next query succeeds → online transition fires again, no idle wait.
    await client.query_for_entities({"customer_name": "Globex"})
    assert client.is_online is True
    assert events == [True, False, True]


@pytest.mark.asyncio
async def test_is_authorized_reflects_token_store():
    """is_authorized() must mirror whether the token store holds tokens."""
    store = _MemoryTokenStore(None)
    client = SalesforceClient(
        token_store=store,
        sf_client_id="cid",
        sf_client_secret="csec",
    )
    assert client.is_authorized() is False

    store.save(dict(_FAKE_TOKENS))
    assert client.is_authorized() is True

    store.clear()
    assert client.is_authorized() is False
