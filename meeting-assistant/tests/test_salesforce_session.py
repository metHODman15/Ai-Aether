"""Tests for backend.salesforce_client session/idle behavior.

The real ``simple_salesforce.Salesforce`` is heavyweight and makes
network calls in ``__init__``; we substitute a lightweight stand-in via
the ``_SF_FACTORY`` seam.  These tests cover:

* ``warm_up`` flips ``is_online`` and triggers the status callback.
* Idle-timeout forces a re-login on the next query.
* Offline → online transitions broadcast exactly one status change.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from backend import salesforce_client as sf_mod
from backend.salesforce_client import SalesforceClient


class _FakeSF:
    """Minimal stand-in for simple_salesforce.Salesforce."""

    instances: list["_FakeSF"] = []

    def __init__(self, **creds):
        self.creds = creds
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


def _new_client(idle_seconds: int = 1800, callback=None) -> SalesforceClient:
    return SalesforceClient(
        username="u@example.com",
        password="pw",
        security_token="tok",
        domain="login",
        idle_refresh_seconds=idle_seconds,
        on_status_change=callback,
    )


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
async def test_idle_timeout_forces_reconnect():
    """After the idle window passes, the next query must rebuild the
    Salesforce session rather than reusing the stale one."""
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
