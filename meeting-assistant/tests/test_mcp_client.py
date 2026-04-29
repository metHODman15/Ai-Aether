"""Tests for the MCP-facing query layer of SalesforceMCPClient.

Covers SOQL composition, response parsing, deduplication, IN-clause
sanitisation, and the offline path when no MCP query tool is available.
The real MCP transport is replaced by FakeSession (see test_salesforce_session).
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Any

import pytest

from backend import mcp_client as mcp_mod
from backend.mcp_client import (
    MCP_TIMEOUT_REASON,
    MCPTimeoutError,
    MCPToolsUnavailable,
    SalesforceMCPClient,
    _build_account_search_soql,
    _build_opportunities_by_accounts_soql,
    _build_opportunity_search_soql,
    _escape_soql_literal,
    _parse_text_content,
    _records_from_response,
)


# ── _escape_soql_literal ─────────────────────────────────────────────────────

def test_escape_soql_quote_and_backslash():
    assert _escape_soql_literal("O'Reilly") == "O\\'Reilly"
    assert _escape_soql_literal("a\\b") == "a\\\\b"
    # Combined.
    assert _escape_soql_literal("a\\'b") == "a\\\\\\'b"


def test_account_soql_uses_escaped_input():
    """Single-quote in user input must not break out of the literal."""
    soql = _build_account_search_soql("O'Reilly")
    # The escape should produce \' inside the quoted literal.
    assert "'%O\\'Reilly%'" in soql
    assert "FROM Account" in soql


def test_opportunity_soql_filters_account_and_name():
    soql = _build_opportunity_search_soql("Acme")
    assert "FROM Opportunity" in soql
    assert "Name LIKE '%Acme%'" in soql
    assert "Account.Name LIKE '%Acme%'" in soql
    assert "ORDER BY CloseDate DESC" in soql


# ── IN-clause sanitisation ───────────────────────────────────────────────────

def test_opportunities_by_accounts_keeps_only_valid_ids():
    ids = {
        "001000000000000",         # 15 chars — valid
        "001000000000000ABC",      # 18 chars — valid
        "abc",                     # too short
        "001'; DROP TABLE--",      # injection attempt
        "001AAAAAAAAAAAA",         # 15 chars — valid
    }
    soql = _build_opportunities_by_accounts_soql(ids)
    assert soql is not None
    # Only the valid IDs survive.
    assert "DROP TABLE" not in soql
    assert "abc" not in soql
    assert "001000000000000" in soql
    assert "001000000000000ABC" in soql
    assert "001AAAAAAAAAAAA" in soql


def test_opportunities_by_accounts_returns_none_for_no_valid_ids():
    assert _build_opportunities_by_accounts_soql({"abc", "xyz"}) is None
    assert _build_opportunities_by_accounts_soql(set()) is None


def test_opportunities_by_accounts_caps_at_20():
    ids = {f"001{str(i).zfill(12)}" for i in range(50)}  # 50 valid 15-char IDs
    soql = _build_opportunities_by_accounts_soql(ids)
    assert soql is not None
    # IN(...) should hold at most 20 quoted IDs.
    assert soql.count("'") <= 40   # 2 quotes per id


# ── _parse_text_content / _records_from_response ─────────────────────────────

class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


class _Result:
    def __init__(self, blocks: list[Any] | str | None) -> None:
        self.content = blocks


def test_parse_text_content_parses_json_text_block():
    payload = {"records": [{"Id": "001"}]}
    parsed = _parse_text_content(_Result([_Block(json.dumps(payload))]))
    assert parsed == payload


def test_parse_text_content_handles_raw_string():
    parsed = _parse_text_content(_Result('{"records": []}'))
    assert parsed == {"records": []}


def test_parse_text_content_returns_none_when_empty():
    assert _parse_text_content(_Result(None)) is None
    assert _parse_text_content(_Result([])) is None


def test_records_from_response_handles_sf_shape():
    assert _records_from_response({"records": [{"Id": "1"}]}) == [{"Id": "1"}]


def test_records_from_response_handles_bare_list():
    assert _records_from_response([{"Id": "1"}, "skip"]) == [{"Id": "1"}]


def test_records_from_response_handles_nested_wrapper():
    nested = {"result": {"records": [{"Id": "X"}]}}
    assert _records_from_response(nested) == [{"Id": "X"}]


def test_records_from_response_empty_inputs():
    assert _records_from_response(None) == []
    assert _records_from_response({}) == []
    assert _records_from_response("nonsense") == []


# ── End-to-end query (with fake session) ─────────────────────────────────────

class FakeSession:
    instances: list["FakeSession"] = []

    def __init__(
        self,
        *,
        records_by_query: list[list[dict]] | None = None,
        tools: list[str] | None = None,
    ) -> None:
        self.tools = tools if tools is not None else ["run_soql_query", "describe_object"]
        self.records_by_query = records_by_query or []
        self.queries: list[str] = []
        self._cursor = 0
        FakeSession.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        pass

    async def list_tools(self):
        class _T: 
            def __init__(self, n): self.name = n
        class _L:
            def __init__(self, ts): self.tools = ts
        return _L([_T(n) for n in self.tools])

    async def call_tool(self, name: str, args: dict):
        if "describe" in name:
            payload = {
                "fields": [
                    {"name": "StageName", "picklistValues": [
                        {"value": "Qualification", "active": True},
                    ]},
                ]
            }
        else:
            self.queries.append(args.get("query", ""))
            if self._cursor < len(self.records_by_query):
                records = self.records_by_query[self._cursor]
                self._cursor += 1
            else:
                records = []
            payload = {"records": records}

        class _Block:
            def __init__(self, t): self.text = t
        class _Res:
            def __init__(self, body): self.content = [_Block(json.dumps(body))]
        return _Res(payload)


def _install_session(monkeypatch, session: FakeSession) -> None:
    @asynccontextmanager
    async def fake_streamable(url: str, headers: dict | None = None):
        yield (None, None)

    monkeypatch.setattr(mcp_mod, "streamablehttp_client", fake_streamable)
    monkeypatch.setattr(mcp_mod, "ClientSession", lambda r, w: session)


class _MemStore:
    def __init__(self, tokens):
        self._t = tokens
    def load(self): return self._t
    def save(self, t): self._t = t
    def clear(self): self._t = None
    def has_tokens(self): return self._t is not None


def _client(monkeypatch, session):
    _install_session(monkeypatch, session)
    return SalesforceMCPClient(
        token_store=_MemStore({
            "access_token": "ACC", "refresh_token": "REF",
            "instance_url": "https://x.salesforce.com",
            "issued_at": time.time(),
        }),
        sf_client_id="cid",
        sf_client_secret="",
        mcp_server_url="https://example.com/mcp",
    )


@pytest.fixture(autouse=True)
def _reset():
    FakeSession.instances = []
    yield


@pytest.mark.asyncio
async def test_query_for_entities_no_search_terms_returns_empty(monkeypatch):
    session = FakeSession()
    client = _client(monkeypatch, session)
    result = await client.query_for_entities({})
    assert result == {
        "accounts": [], "opportunities": [],
        "stage_distribution": [], "amount_timeline": [],
    }
    # No MCP call should be made when there is nothing to search.
    assert session.queries == []


@pytest.mark.asyncio
async def test_query_for_entities_dedupes_accounts_and_opportunities(monkeypatch):
    """Same account / opp returned by multiple search terms must dedupe."""
    session = FakeSession(records_by_query=[
        # 1st term "Acme" — accounts
        [{"Id": "001AAAAAAAAAAAA", "Name": "Acme"}],
        # 1st term "Acme" — opportunities
        [{"Id": "006OOOOOOOOOOOO", "Name": "Deal A", "Amount": 100, "CloseDate": "2025-01-01"}],
        # 2nd term "Acme Corp" — accounts (duplicate id)
        [{"Id": "001AAAAAAAAAAAA", "Name": "Acme"}],
        # 2nd term "Acme Corp" — opportunities (duplicate id)
        [{"Id": "006OOOOOOOOOOOO", "Name": "Deal A", "Amount": 100, "CloseDate": "2025-01-01"}],
        # by-account follow-up — empty
        [],
    ])
    client = _client(monkeypatch, session)

    result = await client.query_for_entities({
        "customer_name": "Acme",
        "keywords": ["Acme Corp"],
    })

    assert len(result["accounts"]) == 1
    assert len(result["opportunities"]) == 1
    assert result["amount_timeline"] == [{"date": "2025-01-01", "amount": 100.0}]


@pytest.mark.asyncio
async def test_mcp_tools_unavailable_marks_offline(monkeypatch):
    """If the MCP server has no SOQL/query tool, return empty + offline.

    Hitting a tools-less server on the very first query (no prior
    warm-up) must:
      * return the documented empty CrmResult shape,
      * leave the client in the offline state,
      * surface ``mcp_tools_unavailable`` to the status callback even
        though the client started offline (reason-change emission).
    """
    events: list[tuple[bool, str | None]] = []

    async def cb(online, reason):
        events.append((online, reason))

    session = FakeSession(tools=[])  # no tools at all
    _install_session(monkeypatch, session)

    client = SalesforceMCPClient(
        token_store=_MemStore({"access_token": "A", "refresh_token": "R",
                               "instance_url": "https://x.salesforce.com",
                               "issued_at": time.time()}),
        sf_client_id="cid",
        sf_client_secret="",
        mcp_server_url="https://example.com/mcp",
        on_status_change=cb,
    )

    result = await client.query_for_entities({"customer_name": "Acme"})
    assert result == {
        "accounts": [],
        "opportunities": [],
        "stage_distribution": [],
        "amount_timeline": [],
    }
    assert client.is_online is False
    # The diagnostic must reach the dashboard — no silent failure.
    assert any(r == "mcp_tools_unavailable" for online, r in events if not online)


@pytest.mark.asyncio
async def test_warm_up_emits_mcp_tools_unavailable_reason(monkeypatch):
    """Warm-up against a tools-less server must surface the canonical
    ``mcp_tools_unavailable`` reason (not a generic exception string)
    so the dashboard shows the same diagnostic at startup as it does
    at query time."""
    events: list[tuple[bool, str | None]] = []

    async def cb(online, reason):
        events.append((online, reason))

    session = FakeSession(tools=[])
    _install_session(monkeypatch, session)

    client = SalesforceMCPClient(
        token_store=_MemStore({"access_token": "A", "refresh_token": "R",
                               "instance_url": "https://x.salesforce.com",
                               "issued_at": time.time()}),
        sf_client_id="cid",
        sf_client_secret="",
        mcp_server_url="https://example.com/mcp",
        on_status_change=cb,
    )

    await client.warm_up()

    assert client.is_online is False
    assert (False, "mcp_tools_unavailable") in events


# ── Timeout handling ─────────────────────────────────────────────────────────
#
# When the Salesforce Hosted MCP Server hangs (network drop, maintenance,
# misconfigured endpoint), every MCP request must be bounded by the
# configured timeout. The dashboard's red Salesforce-offline banner must
# show a clear human-readable reason ("Salesforce MCP server timed out")
# and CRM queries must return the documented empty CrmResult so the UI
# never spins forever.

class _HangingSession:
    """Stand-in MCP session whose first await never completes.

    Mimics a real-world hang where the MCP server accepts the connection
    but stops responding (e.g. mid-deploy, or behind a half-open TCP
    socket). The client's per-request timeout must cancel us cleanly.
    """

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        # Sleep effectively forever — the wait_for() upstream must cancel us.
        await asyncio.sleep(3600)

    async def list_tools(self):  # pragma: no cover - never reached
        await asyncio.sleep(3600)

    async def call_tool(self, name, args):  # pragma: no cover - never reached
        await asyncio.sleep(3600)


def _install_hanging_session(monkeypatch) -> None:
    @asynccontextmanager
    async def fake_streamable(url: str, headers: dict | None = None):
        yield (None, None)

    monkeypatch.setattr(mcp_mod, "streamablehttp_client", fake_streamable)
    monkeypatch.setattr(mcp_mod, "ClientSession", lambda r, w: _HangingSession())


@pytest.mark.asyncio
async def test_warm_up_times_out_and_surfaces_clear_reason(monkeypatch):
    """warm_up against a hanging MCP server must NOT block forever.

    Within the configured timeout the client must:
      * flip is_online to False,
      * emit the canonical human-readable reason on on_status_change,
      * return without raising (warm_up is best-effort).
    """
    events: list[tuple[bool, str | None]] = []

    async def cb(online, reason):
        events.append((online, reason))

    _install_hanging_session(monkeypatch)

    client = SalesforceMCPClient(
        token_store=_MemStore({"access_token": "A", "refresh_token": "R",
                               "instance_url": "https://x.salesforce.com",
                               "issued_at": time.time()}),
        sf_client_id="cid",
        sf_client_secret="",
        mcp_server_url="https://example.com/mcp",
        mcp_timeout_seconds=0.05,
        on_status_change=cb,
    )

    # Bound the test itself so a regression can't hang the whole suite.
    await asyncio.wait_for(client.warm_up(), timeout=5.0)

    assert client.is_online is False
    assert (False, MCP_TIMEOUT_REASON) in events


@pytest.mark.asyncio
async def test_query_times_out_returns_empty_and_marks_offline(monkeypatch):
    """A SOQL query against a hanging MCP server must return the
    documented empty CrmResult, mark the client offline, and emit the
    canonical timeout reason — never freeze the dashboard."""
    events: list[tuple[bool, str | None]] = []

    async def cb(online, reason):
        events.append((online, reason))

    _install_hanging_session(monkeypatch)

    client = SalesforceMCPClient(
        token_store=_MemStore({"access_token": "A", "refresh_token": "R",
                               "instance_url": "https://x.salesforce.com",
                               "issued_at": time.time()}),
        sf_client_id="cid",
        sf_client_secret="",
        mcp_server_url="https://example.com/mcp",
        mcp_timeout_seconds=0.05,
        on_status_change=cb,
    )

    result = await asyncio.wait_for(
        client.query_for_entities({"customer_name": "Acme"}),
        timeout=5.0,
    )

    assert result == {
        "accounts": [],
        "opportunities": [],
        "stage_distribution": [],
        "amount_timeline": [],
    }
    assert client.is_online is False
    assert (False, MCP_TIMEOUT_REASON) in events


@pytest.mark.asyncio
async def test_with_session_raises_mcp_timeout_error_directly(monkeypatch):
    """Internal contract: _with_session converts asyncio.TimeoutError into
    MCPTimeoutError so the public methods can pattern-match cleanly."""
    _install_hanging_session(monkeypatch)

    client = SalesforceMCPClient(
        token_store=_MemStore({"access_token": "A", "refresh_token": "R",
                               "instance_url": "https://x.salesforce.com",
                               "issued_at": time.time()}),
        sf_client_id="cid",
        sf_client_secret="",
        mcp_server_url="https://example.com/mcp",
        mcp_timeout_seconds=0.05,
    )

    async def _noop(_session):  # pragma: no cover - body never reached
        return None

    with pytest.raises(MCPTimeoutError):
        await asyncio.wait_for(client._with_session(_noop), timeout=5.0)
