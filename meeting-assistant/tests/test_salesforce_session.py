"""Tests for SalesforceMCPClient lifecycle and status callbacks.

The real MCP transport is replaced by an in-memory fake at the
``streamablehttp_client`` / ``ClientSession`` seams in ``backend.mcp_client``.
These tests cover:

* ``warm_up`` flips ``is_online`` and triggers the status callback.
* ``warm_up`` with no stored tokens fires ``on_auth_required``.
* Status callback fires only on transitions (no spam).
* Recovery after a transient query failure restores ``crm_online``.
* ``is_authorized()`` mirrors token-store state.
* The OAuth refresh path is exercised when MCP returns 401.
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Any

import pytest

from backend import mcp_client as mcp_mod
from backend.mcp_client import SalesforceMCPClient


# ── In-memory token store ────────────────────────────────────────────────────

class _MemoryTokenStore:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._tokens = initial

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


# ── Fake MCP transport ────────────────────────────────────────────────────────

class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


class _ToolList:
    def __init__(self, names: list[str]) -> None:
        self.tools = [_Tool(n) for n in names]


class _TextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _CallToolResult:
    def __init__(self, payload: Any) -> None:
        body = payload if isinstance(payload, str) else json.dumps(payload)
        self.content = [_TextBlock(body)]


class FakeSession:
    """Minimal stand-in for mcp.ClientSession."""

    instances: list["FakeSession"] = []

    def __init__(
        self,
        *,
        tools: list[str] | None = None,
        query_response: dict | None = None,
        describe_response: dict | None = None,
        raise_on_query: Exception | None = None,
        raise_on_init: Exception | None = None,
    ) -> None:
        self.tools = tools if tools is not None else ["run_soql_query", "describe_object"]
        self.query_response = query_response or {"records": []}
        self.describe_response = describe_response or {
            "fields": [
                {
                    "name": "StageName",
                    "picklistValues": [
                        {"value": "Qualification", "active": True},
                        {"value": "Proposal", "active": True},
                        {"value": "Closed Won", "active": True},
                    ],
                }
            ]
        }
        self.raise_on_query = raise_on_query
        self.raise_on_init = raise_on_init
        self.queries: list[tuple[str, dict]] = []
        FakeSession.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self) -> None:
        if self.raise_on_init is not None:
            raise self.raise_on_init

    async def list_tools(self):
        return _ToolList(self.tools)

    async def call_tool(self, name: str, args: dict):
        self.queries.append((name, args))
        if "describe" in name:
            return _CallToolResult(self.describe_response)
        if self.raise_on_query is not None:
            raise self.raise_on_query
        return _CallToolResult(self.query_response)


def _install_fake_session(monkeypatch, session: FakeSession) -> None:
    """Wire FakeSession into both transport seams."""

    @asynccontextmanager
    async def fake_streamable(url: str, headers: dict | None = None):
        # The real client yields (read, write[, get_session_id]).
        yield (None, None)

    def fake_client_session(read, write):
        return session

    monkeypatch.setattr(mcp_mod, "streamablehttp_client", fake_streamable)
    monkeypatch.setattr(mcp_mod, "ClientSession", fake_client_session)


@pytest.fixture(autouse=True)
def _reset_fake_sessions():
    FakeSession.instances = []
    yield


_DEFAULT = object()


def _new_client(
    tokens: Any = _DEFAULT,
    on_status_change=None,
    on_auth_required=None,
    idle_seconds: int = 1800,
) -> SalesforceMCPClient:
    if tokens is _DEFAULT:
        tokens = dict(_FAKE_TOKENS)
    store = _MemoryTokenStore(tokens)
    return SalesforceMCPClient(
        token_store=store,
        sf_client_id="cid",
        sf_client_secret="",
        mcp_server_url="https://example.com/mcp",
        sf_login_url="https://login.salesforce.com",
        idle_refresh_seconds=idle_seconds,
        on_status_change=on_status_change,
        on_auth_required=on_auth_required,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_warm_up_flips_online_and_invokes_callback(monkeypatch):
    events: list[tuple[bool, str | None]] = []

    async def cb(online: bool, reason: str | None) -> None:
        events.append((online, reason))

    _install_fake_session(monkeypatch, FakeSession())
    client = _new_client(on_status_change=cb)
    assert client.is_online is False

    await client.warm_up()

    assert client.is_online is True
    assert events == [(True, None)]
    # Stage cache populated from describe_object's picklist.
    assert "Proposal" in client.get_stage_names()


@pytest.mark.asyncio
async def test_no_tokens_fires_auth_required(monkeypatch):
    """warm_up with no stored tokens must fire on_auth_required, not crash."""
    auth_required_events: list[int] = []

    async def on_auth_req() -> None:
        auth_required_events.append(1)

    _install_fake_session(monkeypatch, FakeSession())
    client = _new_client(tokens=None, on_auth_required=on_auth_req)

    await client.warm_up()

    assert client.is_online is False
    assert len(auth_required_events) >= 1


@pytest.mark.asyncio
async def test_status_callback_only_fires_on_transition(monkeypatch):
    """Repeated successful queries must not spam crm_online events."""
    events: list[bool] = []

    async def cb(online: bool, reason: str | None) -> None:
        events.append(online)

    _install_fake_session(monkeypatch, FakeSession())
    client = _new_client(on_status_change=cb)

    await client.warm_up()
    await client.query_for_entities({"customer_name": "Acme"})
    await client.query_for_entities({"customer_name": "Globex"})

    # Exactly one offline → online transition expected.
    assert events.count(True) == 1
    assert False not in events


@pytest.mark.asyncio
async def test_recovery_from_query_failure_emits_crm_online(monkeypatch):
    """A transient query failure flips offline; the next success flips online."""
    events: list[bool] = []

    async def cb(online: bool, reason: str | None) -> None:
        events.append(online)

    # Two distinct sessions: first warmup OK, second raises, third OK.
    sessions = [
        FakeSession(),
        FakeSession(raise_on_query=RuntimeError("boom")),
        FakeSession(),
    ]
    cursor = {"i": 0}

    @asynccontextmanager
    async def fake_streamable(url: str, headers: dict | None = None):
        yield (None, None)

    def fake_client_session(read, write):
        s = sessions[cursor["i"]]
        cursor["i"] += 1
        return s

    monkeypatch.setattr(mcp_mod, "streamablehttp_client", fake_streamable)
    monkeypatch.setattr(mcp_mod, "ClientSession", fake_client_session)

    client = _new_client(on_status_change=cb)
    await client.warm_up()
    assert client.is_online is True
    assert events == [True]

    # Failure run.
    await client.query_for_entities({"customer_name": "Acme"})
    assert client.is_online is False
    assert events == [True, False]

    # Recovery run.
    await client.query_for_entities({"customer_name": "Globex"})
    assert client.is_online is True
    assert events == [True, False, True]


@pytest.mark.asyncio
async def test_is_authorized_reflects_token_store(monkeypatch):
    """is_authorized() must mirror whether the token store holds tokens."""
    _install_fake_session(monkeypatch, FakeSession())
    store = _MemoryTokenStore(None)
    client = SalesforceMCPClient(
        token_store=store,
        sf_client_id="cid",
        sf_client_secret="",
        mcp_server_url="https://example.com/mcp",
    )
    assert client.is_authorized() is False

    store.save(dict(_FAKE_TOKENS))
    assert client.is_authorized() is True

    store.clear()
    assert client.is_authorized() is False


@pytest.mark.asyncio
async def test_proactive_idle_refresh_rotates_token(monkeypatch):
    """After the configured idle window, the next query must proactively
    rotate the access token *before* opening the MCP session — matching
    the legacy SF_SESSION_TIMEOUT_MINUTES guarantee."""
    refresh_calls: list[tuple] = []

    def fake_refresh(client_id, refresh_token, login_url, client_secret=""):
        refresh_calls.append((client_id, refresh_token, login_url, client_secret))
        return {
            "access_token": "REFRESHED",
            "refresh_token": refresh_token,
            "instance_url": "https://test.salesforce.com",
            "issued_at": time.time(),
        }

    monkeypatch.setattr(mcp_mod, "refresh_access_token", fake_refresh)
    _install_fake_session(monkeypatch, FakeSession())
    client = _new_client(idle_seconds=1)

    # First query — establishes _last_activity baseline; no refresh yet.
    await client.query_for_entities({"customer_name": "Acme"})
    assert refresh_calls == []

    # Force the activity clock backwards so the next query is "idle".
    client._last_activity = client._last_activity - 5.0

    await client.query_for_entities({"customer_name": "Globex"})
    assert len(refresh_calls) == 1, "Idle threshold crossed → must refresh once."


@pytest.mark.asyncio
async def test_proactive_idle_refresh_disabled_when_zero(monkeypatch):
    """idle_refresh_seconds=0 must disable the proactive rotation entirely."""
    refresh_calls: list[tuple] = []

    def fake_refresh(*a, **kw):
        refresh_calls.append(a)
        return {"access_token": "X", "refresh_token": "Y",
                "instance_url": "Z", "issued_at": time.time()}

    monkeypatch.setattr(mcp_mod, "refresh_access_token", fake_refresh)
    _install_fake_session(monkeypatch, FakeSession())
    client = _new_client(idle_seconds=0)

    await client.query_for_entities({"customer_name": "Acme"})
    client._last_activity = client._last_activity - 10_000.0
    await client.query_for_entities({"customer_name": "Globex"})

    assert refresh_calls == [], "idle_refresh_seconds=0 must skip refresh."


@pytest.mark.asyncio
async def test_offline_reason_change_fires_callback(monkeypatch):
    """An offline → offline transition with a *new* reason must still
    notify the dashboard so users see the latest diagnostic."""
    events: list[tuple[bool, str | None]] = []

    async def cb(online, reason):
        events.append((online, reason))

    _install_fake_session(monkeypatch, FakeSession())
    client = _new_client(tokens=None, on_status_change=cb)

    # First call — fires (False, "auth_required").
    await client.warm_up()
    assert events == [(False, "auth_required")]

    # Force a second offline reason directly through the helper.
    await client._set_online(False, "mcp_tools_unavailable")
    assert events[-1] == (False, "mcp_tools_unavailable")

    # Repeating the same reason must NOT spam.
    await client._set_online(False, "mcp_tools_unavailable")
    assert events.count((False, "mcp_tools_unavailable")) == 1


@pytest.mark.asyncio
async def test_probe_recovers_dashboard_after_timeout(monkeypatch):
    """After an MCP timeout flips the client offline, a single liveness
    probe must restore ``crm_online`` without waiting for a real query."""
    events: list[tuple[bool, str | None]] = []

    async def cb(online: bool, reason: str | None) -> None:
        events.append((online, reason))

    _install_fake_session(monkeypatch, FakeSession())
    client = _new_client(on_status_change=cb)

    # Simulate the offline-after-timeout state set by ``_with_session``.
    await client._set_online(False, mcp_mod.MCP_TIMEOUT_REASON)
    assert client.is_online is False
    assert client._offline_due_to_timeout is True
    assert events == [(False, mcp_mod.MCP_TIMEOUT_REASON)]

    # Salesforce recovers; the probe should flip the dashboard back.
    ok = await client.probe_once()

    assert ok is True
    assert client.is_online is True
    assert client._offline_due_to_timeout is False
    assert events[-1] == (True, None)


@pytest.mark.asyncio
async def test_probe_no_op_when_already_online(monkeypatch):
    """A probe must not open a new MCP session when the client is healthy."""
    _install_fake_session(monkeypatch, FakeSession())
    client = _new_client()
    await client.warm_up()
    assert client.is_online is True

    sessions_before = len(FakeSession.instances)
    ok = await client.probe_once()

    assert ok is True
    assert len(FakeSession.instances) == sessions_before


@pytest.mark.asyncio
async def test_probe_skips_non_timeout_offline_state(monkeypatch):
    """Offline states unrelated to a timeout (auth_required,
    mcp_tools_unavailable) must NOT trigger a liveness probe — they need
    different remediation, not a cheap retry."""
    _install_fake_session(monkeypatch, FakeSession())
    client = _new_client()
    await client._set_online(False, "mcp_tools_unavailable")

    sessions_before = len(FakeSession.instances)
    ok = await client.probe_once()

    assert ok is False
    assert len(FakeSession.instances) == sessions_before


@pytest.mark.asyncio
async def test_probe_pauses_when_no_tokens(monkeypatch):
    """Without OAuth tokens the probe must short-circuit instead of
    opening a session that would fail with auth errors."""
    _install_fake_session(monkeypatch, FakeSession())
    client = _new_client(tokens=None)
    # Force the timeout flag even though there are no tokens, to prove
    # the token check (not just the timeout flag) gates the probe.
    await client._set_online(False, mcp_mod.MCP_TIMEOUT_REASON)
    assert client._offline_due_to_timeout is True

    sessions_before = len(FakeSession.instances)
    ok = await client.probe_once()

    assert ok is False
    assert len(FakeSession.instances) == sessions_before


@pytest.mark.asyncio
async def test_probe_stays_offline_when_mcp_still_timing_out(monkeypatch):
    """If the probe itself times out, the client must stay offline and
    keep the timeout flag set so the next tick will retry."""

    @asynccontextmanager
    async def fake_streamable(url: str, headers: dict | None = None):
        yield (None, None)

    class _StallingSession(FakeSession):
        async def list_tools(self):
            # Simulate an MCP server that never responds.
            await asyncio.sleep(10.0)
            return _ToolList(self.tools)

    stalling = _StallingSession()
    monkeypatch.setattr(mcp_mod, "streamablehttp_client", fake_streamable)
    monkeypatch.setattr(mcp_mod, "ClientSession", lambda r, w: stalling)

    client = _new_client()
    # Tight timeout so the probe gives up quickly inside the test.
    client._mcp_timeout_seconds = 0.05
    await client._set_online(False, mcp_mod.MCP_TIMEOUT_REASON)

    ok = await client.probe_once()

    assert ok is False
    assert client.is_online is False
    assert client._offline_due_to_timeout is True


@pytest.mark.asyncio
async def test_run_recovery_probe_calls_probe_periodically(monkeypatch):
    """The long-running recovery loop must invoke probe_once on each tick
    and stop cleanly when cancelled."""
    _install_fake_session(monkeypatch, FakeSession())
    client = _new_client()

    calls: list[int] = []

    async def fake_probe() -> bool:
        calls.append(1)
        return False

    monkeypatch.setattr(client, "probe_once", fake_probe)

    task = asyncio.create_task(client.run_recovery_probe(interval_seconds=0.01))
    # Let the loop run a few iterations.
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(calls) >= 2


def _install_counting_fake_session(monkeypatch, session: FakeSession) -> list[int]:
    """Like ``_install_fake_session`` but also returns a 1-element list
    whose value tracks how many times the streamable transport was opened.

    Useful for tests that need to assert *no* new MCP session was opened —
    ``FakeSession.instances`` only counts constructor calls, but the
    cooldown contract is about how many times the transport context
    manager is entered.
    """
    opens = [0]

    @asynccontextmanager
    async def fake_streamable(url: str, headers: dict | None = None):
        opens[0] += 1
        yield (None, None)

    monkeypatch.setattr(mcp_mod, "streamablehttp_client", fake_streamable)
    monkeypatch.setattr(mcp_mod, "ClientSession", lambda r, w: session)
    return opens


@pytest.mark.asyncio
async def test_probe_cooldown_short_circuits_rapid_repeats(monkeypatch):
    """A second ``probe_once`` call inside ``RETRY_COOLDOWN_SECONDS`` must
    return the cached result without opening a new MCP session.

    This protects the MCP server from being hammered when reps mash the
    "Retry now" button across multiple tabs (or anything bypassing the
    UI's per-tab debounce) during a real Salesforce outage.
    """
    opens = _install_counting_fake_session(monkeypatch, FakeSession())
    client = _new_client()

    # Drive the client into the offline-due-to-timeout state so probe_once
    # is actually allowed to open an MCP session.
    await client._set_online(False, mcp_mod.MCP_TIMEOUT_REASON)

    # First probe: real round-trip, restores online.
    first = await client.probe_once()
    assert first is True
    assert opens[0] == 1

    # Force the client back offline to make probe_once eligible to run
    # again — if not for the cooldown it would open a fresh MCP session.
    await client._set_online(False, mcp_mod.MCP_TIMEOUT_REASON)

    # Second probe inside the cooldown window: must reuse the cached
    # result (True) and must NOT open a new session.
    second = await client.probe_once()

    assert second is True, "cooldown must return the cached result"
    assert opens[0] == 1, "rapid repeat probe must not open a new MCP session"


@pytest.mark.asyncio
async def test_probe_cooldown_caches_failure_result(monkeypatch):
    """A failed probe's ``False`` result is cached for the cooldown window
    so back-to-back retries during a hard outage don't open extra sessions."""

    opens = [0]

    @asynccontextmanager
    async def fake_streamable(url: str, headers: dict | None = None):
        opens[0] += 1
        yield (None, None)

    class _StallingSession(FakeSession):
        async def list_tools(self):
            await asyncio.sleep(10.0)
            return _ToolList(self.tools)

    stalling = _StallingSession()
    monkeypatch.setattr(mcp_mod, "streamablehttp_client", fake_streamable)
    monkeypatch.setattr(mcp_mod, "ClientSession", lambda r, w: stalling)

    client = _new_client()
    client._mcp_timeout_seconds = 0.05  # fail fast inside the test
    await client._set_online(False, mcp_mod.MCP_TIMEOUT_REASON)

    first = await client.probe_once()
    assert first is False
    assert opens[0] == 1

    # A second probe immediately afterwards must reuse the cached False
    # without opening another stalling session.
    second = await client.probe_once()

    assert second is False
    assert opens[0] == 1


@pytest.mark.asyncio
async def test_probe_after_cooldown_window_opens_fresh_session(monkeypatch):
    """Once the cooldown window has elapsed, the next probe must run a
    real MCP round-trip — the cooldown is a throttle, not a permanent
    cache."""
    opens = _install_counting_fake_session(monkeypatch, FakeSession())
    client = _new_client()
    await client._set_online(False, mcp_mod.MCP_TIMEOUT_REASON)

    await client.probe_once()
    assert opens[0] == 1

    # Simulate the cooldown elapsing by backdating the timestamp.
    client._last_probe_completed_at -= mcp_mod.RETRY_COOLDOWN_SECONDS + 1.0

    # Drive offline again so the probe is eligible to run.
    await client._set_online(False, mcp_mod.MCP_TIMEOUT_REASON)
    await client.probe_once()

    assert opens[0] == 2


@pytest.mark.asyncio
async def test_probe_with_status_distinguishes_fresh_from_coalesced(monkeypatch):
    """``probe_once_with_status`` must mark the first probe as fresh and
    a rapid follow-up as cached with a positive ``age_seconds``.

    This is the contract the dashboard relies on to render
    "Just checked Xs ago, still offline" instead of pretending the
    coalesced click triggered a brand-new MCP round-trip.
    """
    opens = _install_counting_fake_session(monkeypatch, FakeSession())
    client = _new_client()
    await client._set_online(False, mcp_mod.MCP_TIMEOUT_REASON)

    # First probe: real round-trip → fresh result, age 0.
    first = await client.probe_once_with_status()
    assert isinstance(first, mcp_mod.ProbeResult)
    assert first.online is True
    assert first.cached is False
    assert first.age_seconds == 0.0
    assert opens[0] == 1

    # Force the client back offline so the next call would otherwise be
    # eligible to open a fresh session — the cooldown must short-circuit.
    await client._set_online(False, mcp_mod.MCP_TIMEOUT_REASON)
    # Backdate the recorded completion stamp so we get a deterministic,
    # nonzero age in the cached result without sleeping in the test.
    client._last_probe_completed_at -= 1.0

    second = await client.probe_once_with_status()

    assert second.online is True, "must reuse the cached online result"
    assert second.cached is True, "second call within cooldown must be cached"
    assert second.age_seconds >= 1.0, (
        "cached probe must report the age of the underlying result"
    )
    assert opens[0] == 1, "cached probe must not open a new MCP session"


@pytest.mark.asyncio
async def test_probe_with_status_no_op_when_not_eligible(monkeypatch):
    """When the client is offline for a non-timeout reason (or has no
    tokens), ``probe_once_with_status`` returns a fresh-but-offline
    result rather than reporting a cached probe — there's nothing to
    cache because no real probe ever ran."""
    _install_fake_session(monkeypatch, FakeSession())
    client = _new_client()
    await client._set_online(False, "mcp_tools_unavailable")

    result = await client.probe_once_with_status()

    assert result.online is False
    assert result.cached is False
    assert result.age_seconds == 0.0


@pytest.mark.asyncio
async def test_401_triggers_token_refresh_and_retry(monkeypatch):
    """On 401, the client must refresh the access token and retry once."""
    refresh_calls: list[tuple] = []

    def fake_refresh(client_id, refresh_token, login_url, client_secret=""):
        refresh_calls.append((client_id, refresh_token, login_url, client_secret))
        return {
            "access_token": "REFRESHED",
            "refresh_token": refresh_token,
            "instance_url": "https://test.salesforce.com",
            "issued_at": time.time(),
        }

    monkeypatch.setattr(mcp_mod, "refresh_access_token", fake_refresh)

    sessions = [
        FakeSession(),  # warm_up
        FakeSession(raise_on_query=RuntimeError("HTTP 401 Unauthorized")),  # first query
        FakeSession(),  # retry after refresh
    ]
    cursor = {"i": 0}

    @asynccontextmanager
    async def fake_streamable(url: str, headers: dict | None = None):
        yield (None, None)

    def fake_client_session(read, write):
        s = sessions[cursor["i"]]
        cursor["i"] += 1
        return s

    monkeypatch.setattr(mcp_mod, "streamablehttp_client", fake_streamable)
    monkeypatch.setattr(mcp_mod, "ClientSession", fake_client_session)

    client = _new_client()
    await client.warm_up()
    await client.query_for_entities({"customer_name": "Acme"})

    assert len(refresh_calls) == 1
    # All three sessions were opened: warm_up, failing query, retry.
    assert cursor["i"] == 3
    assert client.is_online is True
