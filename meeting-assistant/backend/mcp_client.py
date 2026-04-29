"""Salesforce Hosted MCP Server adapter.

Replaces the legacy ``simple_salesforce`` REST client. Owns:

* The MCP session lifecycle over Streamable HTTP, with the OAuth access
  token injected as a ``Bearer`` token on every request.
* Tool discovery (one ``list_tools()`` per session, regex-matched to find
  a SOQL-execution tool and an sObject describe tool).
* Composing the same SOQL the REST client used to compose, parsing the
  JSON returned in the MCP ``TextContent`` payload, and feeding the
  records through the existing aggregation helpers so :class:`CrmResult`
  is byte-for-byte unchanged.
* The same status/loading/auth callbacks as the legacy client so
  ``app.py`` swaps one constructor and nothing else.

Authentication uses the OAuth + PKCE module (:mod:`backend.oauth`); on
HTTP 401/403 we transparently refresh the access token via the stored
refresh token, rebuild the MCP session, and retry once. If refresh fails
the stored tokens are cleared and ``on_auth_required`` fires so the
dashboard prompts re-authorization.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .entities import Entities
from .oauth import OAuthError, refresh_access_token
from .salesforce_client import (
    CrmResult,
    DEFAULT_STAGE_FALLBACK,
    _SF_ID_RE,
    _amount_timeline,
    _empty_result,
    _has_searchable_input,
    _stage_distribution,
)
from .token_store import TokenStore

logger = logging.getLogger(__name__)

# Slow-query loading indicator threshold (matches the legacy REST client).
SLOW_QUERY_THRESHOLD_SECONDS: float = 2.0

# Stage cache TTL (seconds). Refreshed once per day or after re-auth.
_STAGE_CACHE_TTL: float = 86_400.0

# Regex matchers used to discover MCP tool capabilities. Salesforce's
# Hosted MCP Server publishes tool names like ``run_soql_query``,
# ``execute_soql``, or ``query``; describe-style tools use names like
# ``describe_object``, ``sobject_describe``, etc.
_QUERY_TOOL_RE = re.compile(r"query|soql", re.IGNORECASE)
_DESCRIBE_TOOL_RE = re.compile(r"describe", re.IGNORECASE)


class MCPToolsUnavailable(RuntimeError):
    """Raised when neither a query nor a describe tool can be discovered."""


class MCPTimeoutError(RuntimeError):
    """Raised when an MCP request exceeds the configured per-request timeout.

    Surfaced to the dashboard with a clear human-readable reason so reps
    see *why* the Salesforce-offline banner appeared, instead of a spinner
    that never resolves.
    """


# Default per-MCP-request timeout (seconds). Mirrors the default in
# :class:`backend.config.Config` so the client is safe in test code that
# constructs it directly without a Config object.
DEFAULT_MCP_TIMEOUT_SECONDS: float = 30.0

# Human-readable reason emitted via on_status_change on timeout. The
# frontend renders this verbatim inside the red Salesforce-offline banner.
MCP_TIMEOUT_REASON: str = "Salesforce MCP server timed out"

# Interval between liveness probes used by the background recovery loop
# while the client is offline due to an MCP timeout. 30s gives reps a
# fast green-banner recovery once Salesforce comes back, without
# hammering the MCP server during a longer outage.
RECOVERY_PROBE_INTERVAL_SECONDS: float = 30.0

# Server-side cooldown for ``probe_once``. Any probe call arriving within
# this window after a previous probe completed is short-circuited and
# returns the cached result *without* opening a new MCP session.
#
# Why: ``POST /salesforce/retry`` is the user-facing "Retry now" button
# during a Salesforce outage. The browser disables the button while a
# probe is in flight, but those guards are per-tab — a rep with two
# tabs, a stale tab that refreshes mid-outage, or anything bypassing
# the UI (curl, automated tooling) can still queue probes back-to-back
# and hammer the MCP server during a real incident. A short cooldown
# makes the protection robust regardless of client behaviour: rapid
# repeats coalesce into a single MCP round-trip, while a real recovery
# attempt that arrives after the window still gets a fresh probe.
RETRY_COOLDOWN_SECONDS: float = 3.0


@dataclass(frozen=True)
class ProbeResult:
    """Detailed outcome of a single ``probe_once_with_status`` call.

    Distinguishes a freshly-issued MCP probe from one that was coalesced
    into the most recent result by the cooldown above. The dashboard
    surfaces ``cached`` + ``age_seconds`` to the rep so a throttled
    "Retry now" click reads as "Just checked X seconds ago, still
    offline" instead of looking like a no-op — preserving trust in the
    button without re-enabling spam against the MCP server.

    Attributes:
        online: ``True`` when the client is online after the call.
        cached: ``True`` when the call was short-circuited and reused
            the result of a recent probe (within ``RETRY_COOLDOWN_SECONDS``)
            instead of opening a fresh MCP session. Also ``True`` when
            the client was already online at entry, since no new probe
            was issued.
        age_seconds: How many seconds ago the underlying result was
            observed. ``0.0`` for a fresh probe; small positive value
            for a coalesced one. Always ``>= 0``.
    """

    online: bool
    cached: bool
    age_seconds: float


def _parse_text_content(result: Any) -> Any:
    """Best-effort parse the MCP CallToolResult into a dict or list.

    The Salesforce Hosted MCP Server returns its payload as a JSON string
    inside a single ``TextContent`` block. We tolerate either a list of
    content blocks or a raw string for forward compatibility.
    """
    content = getattr(result, "content", None)
    if content is None:
        return None
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (TypeError, ValueError):
            return content
    # List of content blocks (the documented shape).
    for block in content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except (TypeError, ValueError):
                return text
    return None


def _records_from_response(parsed: Any) -> list[dict[str, Any]]:
    """Coerce an MCP query response into a flat list of record dicts.

    Tolerates the Salesforce REST shape (``{"records": [...]}``), a bare
    list, or an empty/None payload.
    """
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return [r for r in parsed if isinstance(r, dict)]
    if isinstance(parsed, dict):
        recs = parsed.get("records")
        if isinstance(recs, list):
            return [r for r in recs if isinstance(r, dict)]
        # Some implementations wrap the SF response one level deeper.
        for key in ("result", "data", "response"):
            inner = parsed.get(key)
            if isinstance(inner, dict):
                recs = inner.get("records")
                if isinstance(recs, list):
                    return [r for r in recs if isinstance(r, dict)]
    return []


def _escape_soql_literal(value: str) -> str:
    """Escape a string value for safe inclusion in a SOQL string literal.

    Replaces backslashes and single-quotes the way Salesforce expects.
    Mirrors what ``simple_salesforce.format_soql`` did for us in V5.x.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _build_account_search_soql(term: str) -> str:
    safe = _escape_soql_literal(term)
    return (
        "SELECT Id, Name, Industry, Type, Website "
        f"FROM Account WHERE Name LIKE '%{safe}%' LIMIT 10"
    )


def _build_opportunity_search_soql(term: str) -> str:
    safe = _escape_soql_literal(term)
    return (
        "SELECT Id, Name, StageName, Amount, CloseDate, AccountId, Account.Name "
        f"FROM Opportunity WHERE Name LIKE '%{safe}%' "
        f"OR Account.Name LIKE '%{safe}%' "
        "ORDER BY CloseDate DESC LIMIT 25"
    )


def _build_opportunities_by_accounts_soql(account_ids: set[str]) -> str | None:
    safe_ids = [i for i in account_ids if _SF_ID_RE.match(i)][:20]
    if not safe_ids:
        return None
    ids_literal = ",".join(f"'{i}'" for i in safe_ids)
    return (
        "SELECT Id, Name, StageName, Amount, CloseDate, AccountId, Account.Name "
        f"FROM Opportunity WHERE AccountId IN ({ids_literal}) "
        "ORDER BY CloseDate DESC LIMIT 50"
    )


# ── MCP transport seam (overridable in tests) ────────────────────────────────
#
# Tests substitute these with in-memory fakes via monkeypatch so no real
# HTTP connection is opened. Production uses the real ``mcp`` SDK.

def _default_streamablehttp_client(url: str, headers: dict[str, str] | None = None):
    from mcp.client.streamable_http import streamablehttp_client
    return streamablehttp_client(url, headers=headers)


def _default_client_session(read, write):
    from mcp import ClientSession
    return ClientSession(read, write)


# Module-level seams — tests monkeypatch these.
streamablehttp_client = _default_streamablehttp_client
ClientSession = _default_client_session


class SalesforceMCPClient:
    """Salesforce wrapper backed by the Hosted MCP Server.

    Public surface mirrors the legacy ``SalesforceClient`` exactly so
    ``app.py`` only needs to swap the constructor:

    * ``is_online`` / ``is_authorized()`` / ``get_stage_names()``
    * ``warm_up()`` / ``notify_reauthorized()``
    * ``query_for_entities(entities)`` → :class:`CrmResult`
    * Three callbacks: ``on_status_change``, ``on_loading``,
      ``on_auth_required``
    """

    def __init__(
        self,
        token_store: TokenStore,
        sf_client_id: str,
        sf_client_secret: str = "",
        mcp_server_url: str = "",
        sf_login_url: str = "https://login.salesforce.com",
        scopes: str | None = None,
        idle_refresh_seconds: int = 30 * 60,
        mcp_timeout_seconds: float = DEFAULT_MCP_TIMEOUT_SECONDS,
        on_status_change: Callable[[bool, str | None], Awaitable[None]] | None = None,
        on_loading: Callable[[bool], Awaitable[None]] | None = None,
        on_auth_required: Callable[[], Awaitable[None]] | None = None,
    ):
        self._store = token_store
        self._client_id = sf_client_id
        self._client_secret = sf_client_secret
        self._mcp_url = mcp_server_url
        self._login_url = sf_login_url
        self._scopes = scopes
        self._idle_refresh_seconds = idle_refresh_seconds
        # Per-MCP-request timeout. Bounds every call routed through
        # ``_with_session`` (warm-up tool discovery, describe, SOQL query)
        # so a stalled MCP server can never freeze "Connect to Salesforce"
        # or block a query indefinitely. ``<= 0`` disables the bound.
        self._mcp_timeout_seconds = mcp_timeout_seconds
        self._on_status_change = on_status_change
        self._on_loading = on_loading
        self._on_auth_required = on_auth_required

        self._lock = asyncio.Lock()
        # Dedicated lock for ``probe_once`` so the cooldown check and the
        # subsequent MCP round-trip happen atomically: a second probe that
        # races a first one queues here, then sees the freshly-updated
        # cooldown timestamp and returns the cached result instead of
        # opening its own session.
        self._probe_lock = asyncio.Lock()
        # Monotonic timestamp of the most recent ``probe_once`` completion
        # and the boolean it returned. Used to short-circuit rapid repeat
        # probes within ``RETRY_COOLDOWN_SECONDS`` (see constant above).
        self._last_probe_completed_at: float = 0.0
        self._last_probe_result: bool = False
        self._last_activity: float = 0.0
        self._stages: tuple[str, ...] = DEFAULT_STAGE_FALLBACK
        self._stages_fetched_at: float = 0.0
        self._is_online: bool = False
        # Last reason emitted via on_status_change so an offline session that
        # encounters a *new* failure mode (e.g. mcp_tools_unavailable after an
        # earlier auth_required) still surfaces a fresh diagnostic event.
        self._last_status_reason: str | None = None
        # Tracks whether the *current* offline state was caused by an MCP
        # request timeout. The background recovery probe only runs while
        # this is True, so a long outage with a different root cause
        # (auth_required, mcp_tools_unavailable, etc.) does not generate
        # spurious traffic against the MCP server.
        self._offline_due_to_timeout: bool = False
        # Discovered MCP tool names. Intentionally cached across short-lived
        # sessions: tool catalogues for a given MCP server are stable for the
        # lifetime of an OAuth grant, and re-running list_tools() on every
        # query would add a full round-trip to the latency budget. The cache
        # is invalidated on token refresh / re-authorization
        # (``notify_reauthorized`` and the 401-retry path both clear these).
        self._query_tool: str | None = None
        self._describe_tool: str | None = None

    # ── Public observable state ──────────────────────────────────────────

    @property
    def is_online(self) -> bool:
        return self._is_online

    def get_stage_names(self) -> tuple[str, ...]:
        return self._stages

    def is_authorized(self) -> bool:
        return self._store.has_tokens()

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def warm_up(self) -> None:
        """Best-effort startup probe — never raises.

        If OAuth tokens exist, opens a short-lived MCP session to discover
        tools and warm the stage cache. If no tokens exist, fires
        ``on_auth_required`` so the dashboard shows the connect panel.
        """
        if not self._store.has_tokens():
            await self._fire_auth_required()
            return
        try:
            await self._with_session(self._warm_up_actions)
        except MCPToolsUnavailable:
            # Use the canonical reason so the dashboard surfaces the same
            # diagnostic regardless of whether discovery fails at warm-up
            # or on the first query.
            logger.warning(
                "MCP warm_up: required tools not available on the server."
            )
            await self._set_online(False, "mcp_tools_unavailable")
        except MCPTimeoutError:
            # Surface the timeout so "Connect to Salesforce" can never
            # hang forever waiting on a slow/unreachable MCP server.
            await self._set_online(False, MCP_TIMEOUT_REASON)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("MCP warm_up failed: %s", exc)
            await self._set_online(False, str(exc))

    def notify_reauthorized(self) -> None:
        """Called after fresh tokens land so the next query rebuilds state."""
        self._stages_fetched_at = 0.0
        self._last_activity = 0.0
        self._query_tool = None
        self._describe_tool = None

    # ── Background recovery probe ───────────────────────────────────────

    async def probe_once(self) -> bool:
        """Run one MCP liveness probe and return only whether we're online.

        Thin wrapper around :meth:`probe_once_with_status` for callers
        (notably :meth:`run_recovery_probe`) that don't care about the
        cached / age metadata. New callers that need to tell a fresh
        probe apart from a coalesced one should use
        :meth:`probe_once_with_status` directly.
        """
        result = await self.probe_once_with_status()
        return result.online

    async def probe_once_with_status(self) -> ProbeResult:
        """Run one MCP liveness probe and report fresh-vs-coalesced status.

        Returns a :class:`ProbeResult` describing whether the client is
        online, whether this call was coalesced into a recent probe by
        the cooldown, and how old the underlying result is. The probe
        itself is intentionally cheap: it opens a short MCP session and
        calls ``list_tools()``. Success flips the dashboard back to
        online without waiting for a conversational entity to trigger
        a full SOQL query.

        The probe is a no-op (and returns immediately) when:

        * the client is already online — nothing to recover; reported
          as ``cached=True`` with the age of the last probe so the UI
          can still distinguish "we just confirmed this" from
          "we're freshly checking",
        * the offline state was *not* caused by an MCP timeout — other
          failure modes (auth_required, mcp_tools_unavailable) need
          different remediation, not a liveness check,
        * no OAuth tokens are present — pause until the user reconnects,
        * a previous probe completed within ``RETRY_COOLDOWN_SECONDS`` —
          rapid repeat clicks (e.g. a rep mashing "Retry now" across two
          tabs during a real outage) coalesce into one MCP round-trip
          and reuse the most recent result. See the constant's docstring
          for the rationale.
        """
        if self._is_online:
            # No probe needed; surface this as cached so the UI shows
            # "Just checked Xs ago" rather than implying a fresh probe.
            return ProbeResult(
                online=True,
                cached=True,
                age_seconds=self._age_of_last_probe(),
            )
        if not self._offline_due_to_timeout:
            return ProbeResult(online=False, cached=False, age_seconds=0.0)
        if not self._store.has_tokens():
            return ProbeResult(online=False, cached=False, age_seconds=0.0)

        # Serialize probes so the cooldown check and the MCP round-trip
        # are atomic — otherwise two probes that both pass the cooldown
        # check could each open a session before either records its
        # completion timestamp. The lock is held only for the duration
        # of one probe so a fresh call after the cooldown window does
        # not wait behind an unrelated stale probe.
        async with self._probe_lock:
            # The cooldown only applies once at least one probe has
            # completed — otherwise the very first probe of the process
            # would be short-circuited by an artificial "age 0" reading.
            if self._last_probe_completed_at > 0.0:
                age = time.monotonic() - self._last_probe_completed_at
                if age < RETRY_COOLDOWN_SECONDS:
                    logger.debug(
                        "Salesforce probe within %.1fs cooldown; returning cached result.",
                        RETRY_COOLDOWN_SECONDS,
                    )
                    return ProbeResult(
                        online=self._last_probe_result,
                        cached=True,
                        age_seconds=max(0.0, age),
                    )

            try:
                await self._with_session(self._probe_actions)
            except MCPTimeoutError:
                logger.debug(
                    "Salesforce recovery probe: MCP still timing out; staying offline."
                )
                self._record_probe_completion(False)
                return ProbeResult(online=False, cached=False, age_seconds=0.0)
            except _AuthRetryNeeded:
                logger.debug(
                    "Salesforce recovery probe: auth required; pausing probe."
                )
                self._record_probe_completion(False)
                return ProbeResult(online=False, cached=False, age_seconds=0.0)
            except Exception as exc:
                logger.debug("Salesforce recovery probe failed: %s", exc)
                self._record_probe_completion(False)
                return ProbeResult(online=False, cached=False, age_seconds=0.0)
            result = self._is_online
            self._record_probe_completion(result)
            return ProbeResult(online=result, cached=False, age_seconds=0.0)

    def _age_of_last_probe(self) -> float:
        """Seconds elapsed since the last probe completion timestamp.

        Returns ``0.0`` before any probe has ever completed, so callers
        never see a nonsensical age computed from the monotonic origin.
        """
        if self._last_probe_completed_at <= 0.0:
            return 0.0
        return max(0.0, time.monotonic() - self._last_probe_completed_at)

    def _record_probe_completion(self, result: bool) -> None:
        """Stamp the cooldown timestamp + cached result for the next probe."""
        self._last_probe_completed_at = time.monotonic()
        self._last_probe_result = result

    async def _probe_actions(self, session: Any) -> None:
        """Cheap liveness call inside an MCP session — list_tools()."""
        await session.list_tools()
        await self._set_online(True)

    async def run_recovery_probe(
        self, interval_seconds: float = RECOVERY_PROBE_INTERVAL_SECONDS
    ) -> None:
        """Long-running task: probe periodically while offline due to timeout.

        Designed to be launched alongside ``warm_up()`` from the FastAPI
        lifespan. Each tick sleeps ``interval_seconds`` then calls
        :meth:`probe_once`, which short-circuits when no probe is needed.
        Cancellation propagates so the lifespan's cleanup cancels this
        cleanly on shutdown.
        """
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await self.probe_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                logger.exception("Salesforce recovery probe iteration crashed")

    # ── Query API ────────────────────────────────────────────────────────

    async def query_for_entities(self, entities: Entities) -> CrmResult:
        if not _has_searchable_input(entities):
            return _empty_result()

        if not self._store.has_tokens():
            await self._fire_auth_required()
            return _empty_result()

        # Proactive idle refresh: if the configured idle window has passed
        # since the last activity, rotate the access token *before* opening
        # an MCP session. This avoids stale-token 401s during long meetings
        # — the same guarantee the legacy session_timeout_minutes flag gave.
        await self._maybe_proactive_refresh()

        loading_emitted = False
        slow_query_task: asyncio.Task | None = None

        async def _maybe_emit_loading() -> None:
            nonlocal loading_emitted
            await asyncio.sleep(SLOW_QUERY_THRESHOLD_SECONDS)
            loading_emitted = True
            await self._emit_loading(True)

        try:
            slow_query_task = asyncio.create_task(_maybe_emit_loading())

            async def _do_query(session: Any) -> CrmResult:
                return await self._query_via_session(session, entities)

            try:
                result = await self._with_session(_do_query)
            except _AuthRetryNeeded:
                refreshed = await self._do_token_refresh()
                if refreshed is None:
                    await self._fire_auth_required()
                    return _empty_result()
                # Rebuild session with the new token and try once more.
                self._query_tool = None
                self._describe_tool = None
                try:
                    result = await self._with_session(_do_query)
                except MCPTimeoutError:
                    await self._set_online(False, MCP_TIMEOUT_REASON)
                    return _empty_result()
                except Exception as exc2:
                    logger.warning("MCP query failed after token refresh: %s", exc2)
                    await self._set_online(False, str(exc2))
                    return _empty_result()
            except MCPToolsUnavailable:
                logger.warning("Required MCP tools not available on the server.")
                await self._set_online(False, "mcp_tools_unavailable")
                return _empty_result()
            except MCPTimeoutError:
                # Bounded wait elapsed — surface the canonical reason so
                # the dashboard banner explains *why* CRM data is empty
                # instead of leaving a spinner running.
                await self._set_online(False, MCP_TIMEOUT_REASON)
                return _empty_result()
            except Exception as exc:
                logger.warning("MCP query failed: %s", exc)
                await self._set_online(False, str(exc))
                return _empty_result()

            await self._set_online(True)
            return result

        finally:
            if slow_query_task is not None and not slow_query_task.done():
                slow_query_task.cancel()
                try:
                    await slow_query_task
                except asyncio.CancelledError:
                    pass
            if loading_emitted:
                await self._emit_loading(False)
            self._last_activity = time.monotonic()

    # ── Session management ──────────────────────────────────────────────

    async def _with_session(self, fn: Callable[[Any], Awaitable[Any]]) -> Any:
        """Open a fresh MCP session, run ``fn(session)``, then close it.

        Each query opens its own short-lived session — keeping a long-lived
        connection across an idle meeting risks token expiry mid-flight,
        and the streamable-HTTP transport is cheap to reopen.

        The entire transport-open → initialize → ``fn(session)`` flow is
        bounded by ``self._mcp_timeout_seconds``. On timeout we raise
        :class:`MCPTimeoutError` so the caller can flip the dashboard
        offline with a clear reason instead of awaiting forever — the
        primary operational risk flagged in the V6.0.0 architect review.
        """
        async with self._lock:
            tokens = self._store.load()
            if tokens is None:
                raise _AuthRetryNeeded()
            access_token = tokens.get("access_token")
            if not access_token:
                raise _AuthRetryNeeded()
            headers = {"Authorization": f"Bearer {access_token}"}

            async def _run() -> Any:
                transport = streamablehttp_client(self._mcp_url, headers=headers)
                async with transport as transport_streams:
                    # streamablehttp_client may yield (read, write) or
                    # (read, write, get_session_id). Tolerate both.
                    read, write = transport_streams[0], transport_streams[1]
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        return await fn(session)

            try:
                if self._mcp_timeout_seconds and self._mcp_timeout_seconds > 0:
                    return await asyncio.wait_for(
                        _run(), timeout=self._mcp_timeout_seconds
                    )
                return await _run()
            except asyncio.TimeoutError as exc:
                logger.warning(
                    "MCP request exceeded %.1fs timeout against %s.",
                    self._mcp_timeout_seconds, self._mcp_url,
                )
                raise MCPTimeoutError(MCP_TIMEOUT_REASON) from exc
            except Exception as exc:
                if _is_auth_error(exc):
                    raise _AuthRetryNeeded() from exc
                raise

    async def _warm_up_actions(self, session: Any) -> None:
        """Discover tools and refresh the stage cache. Caller holds the lock."""
        await self._discover_tools(session)
        if time.time() - self._stages_fetched_at > _STAGE_CACHE_TTL:
            await self._refresh_stages(session)
        await self._set_online(True)

    async def _discover_tools(self, session: Any) -> None:
        """Inspect the MCP server's tool catalogue and pick the SOQL tool."""
        listed = await session.list_tools()
        tools = getattr(listed, "tools", None) or []
        query_tool: str | None = None
        describe_tool: str | None = None
        for tool in tools:
            name = getattr(tool, "name", None) or ""
            if not name:
                continue
            if query_tool is None and _QUERY_TOOL_RE.search(name):
                query_tool = name
            if describe_tool is None and _DESCRIBE_TOOL_RE.search(name):
                describe_tool = name
        if query_tool is None:
            raise MCPToolsUnavailable(
                "No SOQL/query-capable tool found on the MCP server."
            )
        self._query_tool = query_tool
        self._describe_tool = describe_tool
        logger.info(
            "Discovered MCP tools: query=%s, describe=%s",
            query_tool, describe_tool,
        )

    async def _refresh_stages(self, session: Any) -> None:
        """Best-effort load of the live Opportunity.StageName picklist."""
        if self._describe_tool is None:
            logger.info(
                "No describe-capable MCP tool; using fallback stage list."
            )
            self._stages = DEFAULT_STAGE_FALLBACK
            return
        try:
            result = await session.call_tool(
                self._describe_tool, {"sObjectType": "Opportunity"}
            )
            parsed = _parse_text_content(result) or {}
            stages: list[str] = []
            fields = (parsed.get("fields") if isinstance(parsed, dict) else None) or []
            for field in fields:
                if not isinstance(field, dict):
                    continue
                if field.get("name") == "StageName":
                    for entry in field.get("picklistValues") or []:
                        if isinstance(entry, dict) and entry.get("active") and entry.get("value"):
                            stages.append(entry["value"])
                    break
            if stages:
                self._stages = tuple(stages)
                self._stages_fetched_at = time.time()
                logger.info("Loaded %d Opportunity stages from MCP describe.", len(stages))
            else:
                self._stages = DEFAULT_STAGE_FALLBACK
        except Exception as exc:
            logger.warning(
                "MCP describe of Opportunity.StageName failed (%s); using fallback.",
                exc,
            )
            self._stages = DEFAULT_STAGE_FALLBACK

    # ── Query execution ────────────────────────────────────────────────

    async def _query_via_session(self, session: Any, entities: Entities) -> CrmResult:
        """Compose the same SOQL the REST client built and run it via MCP."""
        if self._query_tool is None:
            await self._discover_tools(session)
        # Stages are best-effort; do not fail the whole query if absent.
        if time.time() - self._stages_fetched_at > _STAGE_CACHE_TTL:
            await self._refresh_stages(session)

        search_terms: list[str] = []
        for key in ("customer_name", "contact_name"):
            value = entities.get(key)
            if value:
                search_terms.append(value)
        for kw in entities.get("keywords") or []:
            if kw and kw not in search_terms:
                search_terms.append(kw)

        accounts: list[dict[str, Any]] = []
        opportunities: list[dict[str, Any]] = []
        seen_account_ids: set[str] = set()
        seen_opp_ids: set[str] = set()

        for term in search_terms[:5]:
            for acc in await self._run_soql(session, _build_account_search_soql(term)):
                aid = acc.get("Id")
                if aid and aid not in seen_account_ids:
                    seen_account_ids.add(aid)
                    accounts.append(acc)
            for opp in await self._run_soql(session, _build_opportunity_search_soql(term)):
                oid = opp.get("Id")
                if oid and oid not in seen_opp_ids:
                    seen_opp_ids.add(oid)
                    opportunities.append(opp)

        by_account_soql = _build_opportunities_by_accounts_soql(seen_account_ids)
        if by_account_soql is not None:
            for opp in await self._run_soql(session, by_account_soql):
                oid = opp.get("Id")
                if oid and oid not in seen_opp_ids:
                    seen_opp_ids.add(oid)
                    opportunities.append(opp)

        return CrmResult(
            accounts=accounts[:25],
            opportunities=opportunities[:50],
            stage_distribution=_stage_distribution(opportunities),
            amount_timeline=_amount_timeline(opportunities),
        )

    async def _run_soql(self, session: Any, soql: str) -> list[dict[str, Any]]:
        assert self._query_tool is not None  # _discover_tools guarantees this
        result = await session.call_tool(self._query_tool, {"query": soql})
        parsed = _parse_text_content(result)
        return _records_from_response(parsed)

    # ── Token + callback plumbing ──────────────────────────────────────

    async def _do_token_refresh(self) -> dict[str, Any] | None:
        tokens = self._store.load()
        if not tokens or not tokens.get("refresh_token"):
            return None
        try:
            new_tokens = await asyncio.to_thread(
                refresh_access_token,
                self._client_id,
                tokens["refresh_token"],
                self._login_url,
                self._client_secret,
            )
            merged = {**tokens, **new_tokens}
            self._store.save(merged)
            logger.info("Salesforce access token refreshed successfully.")
            return merged
        except OAuthError as exc:
            logger.warning("OAuth token refresh failed (%s); clearing tokens.", exc)
            self._store.clear()
            return None

    async def _maybe_proactive_refresh(self) -> None:
        """Rotate the OAuth token if we've been idle past the threshold.

        Only kicks in when ``idle_refresh_seconds > 0`` and the client has
        already served at least one query (``_last_activity > 0``).
        Rebuilds tool/stage caches after a successful rotation so the next
        session starts clean.
        """
        if self._idle_refresh_seconds <= 0 or self._last_activity <= 0.0:
            return
        idle = time.monotonic() - self._last_activity
        if idle < self._idle_refresh_seconds:
            return
        logger.info(
            "Idle for %.0fs (>= %ds threshold); proactively refreshing Salesforce token.",
            idle, self._idle_refresh_seconds,
        )
        refreshed = await self._do_token_refresh()
        if refreshed is None:
            await self._fire_auth_required()
            return
        # Force fresh tool discovery + stage cache on the next session.
        self._query_tool = None
        self._describe_tool = None
        self._stages_fetched_at = 0.0
        # Reset so a subsequent quick query doesn't re-trigger immediately.
        self._last_activity = time.monotonic()

    async def _fire_auth_required(self) -> None:
        await self._set_online(False, "auth_required")
        if self._on_auth_required is not None:
            try:
                await self._on_auth_required()
            except Exception:
                logger.exception("on_auth_required handler failed")

    async def _set_online(self, online: bool, reason: str | None = None) -> None:
        # Emit on:
        #   1. an actual online/offline transition, OR
        #   2. a *new* offline reason (offline → offline with different cause).
        # Going online twice in a row stays silent — that path can never
        # carry a meaningful reason.
        is_transition = self._is_online != online
        is_new_offline_reason = (
            online is False
            and reason is not None
            and reason != self._last_status_reason
        )
        if not (is_transition or is_new_offline_reason):
            return
        self._is_online = online
        self._last_status_reason = reason
        # Keep the recovery-probe gate in sync with the *current* offline
        # cause: only MCP timeouts warrant a background liveness probe.
        # Transitioning online (or going offline for any other reason)
        # clears the flag so the probe loop stays idle.
        if online:
            self._offline_due_to_timeout = False
        else:
            self._offline_due_to_timeout = (reason == MCP_TIMEOUT_REASON)
        logger.info(
            "Salesforce status -> %s (%s)",
            "online" if online else "offline",
            reason or "",
        )
        if self._on_status_change is not None:
            try:
                await self._on_status_change(online, reason)
            except Exception:
                logger.exception("on_status_change handler failed")

    async def _emit_loading(self, loading: bool) -> None:
        if self._on_loading is None:
            return
        try:
            await self._on_loading(loading)
        except Exception:
            logger.exception("on_loading handler failed")


class _AuthRetryNeeded(RuntimeError):
    """Internal sentinel: caller should refresh tokens and retry once."""


def _is_auth_error(exc: Exception) -> bool:
    """Best-effort detector for HTTP 401/403 surfaced by the MCP transport."""
    msg = str(exc)
    if "401" in msg or "403" in msg or "Unauthorized" in msg or "Forbidden" in msg:
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return status in (401, 403)
