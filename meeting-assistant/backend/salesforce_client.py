"""Salesforce REST API client wrapper (OAuth 2.0 Web Server Flow).

Authenticates using OAuth access tokens obtained through the browser-based
OAuth flow and stored in TokenStore (encrypted SQLite). When an access
token expires the client transparently uses the refresh token to obtain
a new one. If the refresh token is revoked or expired the stored tokens
are cleared and the ``on_auth_required`` callback fires so the UI can
prompt the user to reauthorize.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable, TypedDict

from simple_salesforce import Salesforce
from simple_salesforce.exceptions import (
    SalesforceAuthenticationFailed,
    SalesforceError,
    SalesforceExpiredSession,
)
from simple_salesforce.format import format_soql

from .entities import Entities
from .oauth import OAuthError, refresh_access_token
from .token_store import TokenStore

logger = logging.getLogger(__name__)

SLOW_QUERY_THRESHOLD_SECONDS: float = 2.0

# Stage cache refresh interval (seconds). Stages are re-fetched once per day
# or after re-authorization.
_STAGE_CACHE_TTL: float = 86_400.0

# Conservative fallback used if the Opportunity.StageName describe call
# fails (e.g. permission issue on a fresh sandbox).
DEFAULT_STAGE_FALLBACK: tuple[str, ...] = (
    "Prospecting",
    "Qualification",
    "Needs Analysis",
    "Value Proposition",
    "Id. Decision Makers",
    "Perception Analysis",
    "Proposal/Price Quote",
    "Negotiation/Review",
    "Closed Won",
    "Closed Lost",
)


class CrmResult(TypedDict):
    accounts: list[dict[str, Any]]
    opportunities: list[dict[str, Any]]
    stage_distribution: list[dict[str, Any]]
    amount_timeline: list[dict[str, Any]]


def _empty_result() -> CrmResult:
    return CrmResult(
        accounts=[], opportunities=[], stage_distribution=[], amount_timeline=[]
    )


class SalesforceClient:
    """Salesforce wrapper using OAuth tokens with auto-refresh, idle timeout,
    and dynamic stage cache."""

    def __init__(
        self,
        token_store: TokenStore,
        sf_client_id: str,
        sf_client_secret: str,
        sf_login_url: str = "https://login.salesforce.com",
        idle_refresh_seconds: int = 30 * 60,
        on_status_change: Callable[[bool, str | None], Awaitable[None]] | None = None,
        on_loading: Callable[[bool], Awaitable[None]] | None = None,
        on_auth_required: Callable[[], Awaitable[None]] | None = None,
    ):
        self._store = token_store
        self._client_id = sf_client_id
        self._client_secret = sf_client_secret
        self._login_url = sf_login_url
        self._idle_refresh_seconds = idle_refresh_seconds
        self._on_status_change = on_status_change
        self._on_loading = on_loading
        self._on_auth_required = on_auth_required

        self._sf: Salesforce | None = None
        self._sf_lock = asyncio.Lock()
        self._last_activity: float = 0.0
        self._stages: tuple[str, ...] = DEFAULT_STAGE_FALLBACK
        self._stages_fetched_at: float = 0.0
        self._is_online: bool = False

    # ── Public observable state ──────────────────────────────────────────

    @property
    def is_online(self) -> bool:
        return self._is_online

    def get_stage_names(self) -> tuple[str, ...]:
        """Return the cached Opportunity.StageName picklist (or the fallback)."""
        return self._stages

    def is_authorized(self) -> bool:
        """Return True if OAuth tokens are stored (does not validate liveness)."""
        return self._store.has_tokens()

    # ── Connection management ────────────────────────────────────────────

    def _build_sf_from_tokens(self, tokens: dict[str, Any]) -> Salesforce:
        """Construct a simple_salesforce.Salesforce from stored OAuth tokens."""
        return Salesforce(
            instance_url=tokens["instance_url"],
            session_id=tokens["access_token"],
        )

    async def _do_token_refresh(self) -> dict[str, Any] | None:
        """Try to refresh the access token. Returns new token dict or None."""
        tokens = self._store.load()
        if not tokens or not tokens.get("refresh_token"):
            return None
        try:
            new_tokens = await asyncio.to_thread(
                refresh_access_token,
                self._client_id,
                self._client_secret,
                tokens["refresh_token"],
                self._login_url,
            )
            # Merge: keep the existing refresh_token if new response omits it.
            merged = {**tokens, **new_tokens}
            self._store.save(merged)
            logger.info("Salesforce access token refreshed successfully.")
            return merged
        except OAuthError as exc:
            logger.warning("OAuth token refresh failed (%s); clearing tokens.", exc)
            self._store.clear()
            return None

    async def _ensure_session(self, force: bool = False) -> Salesforce | None:
        """Return a ready Salesforce session, refreshing if stale or forced."""
        async with self._sf_lock:
            tokens = self._store.load()
            if tokens is None:
                # No tokens — user has never authorized.
                await self._fire_auth_required()
                return None

            now = time.monotonic()
            stale = (
                self._last_activity > 0
                and now - self._last_activity > self._idle_refresh_seconds
            )
            if force or self._sf is None or stale:
                if stale and self._sf is not None:
                    logger.info(
                        "Salesforce session idle for >%ds; refreshing token proactively.",
                        self._idle_refresh_seconds,
                    )
                    # Proactive refresh on idle to avoid expiry mid-query.
                    refreshed = await self._do_token_refresh()
                    if refreshed:
                        tokens = refreshed
                    else:
                        await self._fire_auth_required()
                        return None

                try:
                    self._sf = await asyncio.to_thread(
                        self._build_sf_from_tokens, tokens
                    )
                    self._last_activity = now
                    await self._set_online(True)
                    # Refresh dynamic stages on (re)connect or if cache is stale.
                    if time.time() - self._stages_fetched_at > _STAGE_CACHE_TTL:
                        await self._refresh_stages_unlocked()
                except Exception as exc:
                    self._sf = None
                    logger.warning("Salesforce session build failed: %s", exc)
                    await self._set_online(False, str(exc))
                    return None
            return self._sf

    async def _fire_auth_required(self) -> None:
        """Emit auth_required and set offline — does NOT raise."""
        await self._set_online(False, "auth_required")
        if self._on_auth_required is not None:
            try:
                await self._on_auth_required()
            except Exception:
                logger.exception("on_auth_required handler failed")

    async def _set_online(self, online: bool, reason: str | None = None) -> None:
        if self._is_online == online:
            return
        self._is_online = online
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

    async def _refresh_stages_unlocked(self) -> None:
        """Refresh the cached StageName picklist. Caller must hold the lock."""
        sf = self._sf
        if sf is None:
            return
        try:
            described = await asyncio.to_thread(lambda: sf.Opportunity.describe())
            stages: list[str] = []
            for field in described.get("fields", []):
                if field.get("name") == "StageName":
                    for entry in field.get("picklistValues", []) or []:
                        if entry.get("active") and entry.get("value"):
                            stages.append(entry["value"])
                    break
            if stages:
                self._stages = tuple(stages)
                self._stages_fetched_at = time.time()
                logger.info("Loaded %d Opportunity stages from Salesforce.", len(stages))
            else:
                logger.warning("Opportunity.StageName picklist empty; using fallback.")
                self._stages = DEFAULT_STAGE_FALLBACK
        except Exception as exc:
            logger.warning(
                "Could not describe Opportunity.StageName (%s); using fallback list.",
                exc,
            )
            self._stages = DEFAULT_STAGE_FALLBACK

    async def warm_up(self) -> None:
        """Best-effort startup check — never raises.

        If OAuth tokens exist, establishes the Salesforce session and warms
        up the stage cache. If no tokens exist, fires auth_required so the
        UI shows the authorization panel immediately.
        """
        await self._ensure_session()

    def notify_reauthorized(self) -> None:
        """Call after new tokens are stored so the next query rebuilds the session."""
        async_context = asyncio.get_event_loop()
        if async_context:
            # Schedule cache invalidation + stage refresh on the next query.
            self._stages_fetched_at = 0.0
        self._sf = None
        self._last_activity = 0.0

    # ── Query API ────────────────────────────────────────────────────────

    async def _emit_loading(self, loading: bool) -> None:
        """Notify the caller that a slow query is in progress (or has finished)."""
        if self._on_loading is None:
            return
        try:
            await self._on_loading(loading)
        except Exception:
            logger.exception("on_loading handler failed")

    async def _handle_auth_error(self) -> Salesforce | None:
        """Attempt token refresh after auth error. Returns new session or None."""
        async with self._sf_lock:
            self._sf = None
        refreshed = await self._do_token_refresh()
        if refreshed is None:
            await self._fire_auth_required()
            return None
        async with self._sf_lock:
            try:
                self._sf = await asyncio.to_thread(
                    self._build_sf_from_tokens, refreshed
                )
                return self._sf
            except Exception as exc:
                self._sf = None
                await self._set_online(False, str(exc))
                return None

    async def query_for_entities(self, entities: Entities) -> CrmResult:
        if not _has_searchable_input(entities):
            return _empty_result()

        sf = await self._ensure_session()
        if sf is None:
            return _empty_result()

        loading_emitted = False
        slow_query_task: asyncio.Task | None = None

        async def _maybe_emit_loading() -> None:
            nonlocal loading_emitted
            await asyncio.sleep(SLOW_QUERY_THRESHOLD_SECONDS)
            loading_emitted = True
            await self._emit_loading(True)

        try:
            slow_query_task = asyncio.create_task(_maybe_emit_loading())
            result = await asyncio.to_thread(self._query_sync, sf, entities)
            await self._set_online(True)
            return result

        except SalesforceExpiredSession as exc:
            logger.info("Salesforce session expired (%s); refreshing token.", exc)
            sf = await self._handle_auth_error()
            if sf is None:
                return _empty_result()
            try:
                result = await asyncio.to_thread(self._query_sync, sf, entities)
                await self._set_online(True)
                return result
            except SalesforceError as exc2:
                logger.warning("Salesforce query failed after token refresh: %s", exc2)
                async with self._sf_lock:
                    self._sf = None
                await self._set_online(False, str(exc2))
                return _empty_result()

        except SalesforceAuthenticationFailed as exc:
            logger.warning("Salesforce auth failed mid-session: %s", exc)
            sf = await self._handle_auth_error()
            if sf is None:
                return _empty_result()
            try:
                result = await asyncio.to_thread(self._query_sync, sf, entities)
                await self._set_online(True)
                return result
            except SalesforceError as exc2:
                logger.warning("Salesforce query failed after re-auth: %s", exc2)
                async with self._sf_lock:
                    self._sf = None
                await self._set_online(False, str(exc2))
                return _empty_result()

        except SalesforceError as exc:
            logger.warning("Salesforce query failed: %s", exc)
            async with self._sf_lock:
                self._sf = None
            await self._set_online(False, str(exc))
            return _empty_result()

        except Exception as exc:
            logger.warning("Unexpected Salesforce error: %s", exc)
            async with self._sf_lock:
                self._sf = None
            await self._set_online(False, str(exc))
            return _empty_result()

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

    def _query_sync(self, sf: Salesforce, entities: Entities) -> CrmResult:
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
            accs = self._search_accounts(sf, term)
            for a in accs:
                if a["Id"] not in seen_account_ids:
                    seen_account_ids.add(a["Id"])
                    accounts.append(a)
            opps = self._search_opportunities(sf, term)
            for o in opps:
                if o["Id"] not in seen_opp_ids:
                    seen_opp_ids.add(o["Id"])
                    opportunities.append(o)

        if seen_account_ids:
            opps = self._opportunities_by_accounts(sf, seen_account_ids)
            for o in opps:
                if o["Id"] not in seen_opp_ids:
                    seen_opp_ids.add(o["Id"])
                    opportunities.append(o)

        return CrmResult(
            accounts=accounts[:25],
            opportunities=opportunities[:50],
            stage_distribution=_stage_distribution(opportunities),
            amount_timeline=_amount_timeline(opportunities),
        )

    # Salesforce record IDs are exactly 15 or 18 alphanumeric characters.
    # Validating before interpolation prevents any injection via the IN clause.
    _SF_ID_RE = re.compile(r"^[A-Za-z0-9]{15}$|^[A-Za-z0-9]{18}$")

    @staticmethod
    def _search_accounts(sf: Salesforce, term: str) -> list[dict[str, Any]]:
        # format_soql quotes and escapes the value; pass the wildcard-wrapped
        # search term so the LIKE pattern is built safely without manual escaping.
        soql = format_soql(
            "SELECT Id, Name, Industry, Type, Website "
            "FROM Account WHERE Name LIKE {term} LIMIT 10",
            term=f"%{term}%",
        )
        return list(sf.query(soql).get("records", []))

    @staticmethod
    def _search_opportunities(sf: Salesforce, term: str) -> list[dict[str, Any]]:
        soql = format_soql(
            "SELECT Id, Name, StageName, Amount, CloseDate, AccountId, Account.Name "
            "FROM Opportunity WHERE Name LIKE {term} "
            "OR Account.Name LIKE {term} "
            "ORDER BY CloseDate DESC LIMIT 25",
            term=f"%{term}%",
        )
        return list(sf.query(soql).get("records", []))

    @classmethod
    def _opportunities_by_accounts(
        cls, sf: Salesforce, account_ids: set[str]
    ) -> list[dict[str, Any]]:
        # Validate each ID against the Salesforce 15/18-char alphanumeric format
        # before interpolating into the IN clause so malformed IDs cannot inject SQL.
        safe_ids = [i for i in account_ids if cls._SF_ID_RE.match(i)][:20]
        if not safe_ids:
            return []
        # IDs are purely alphanumeric after validation — format_soql does not
        # support list bind variables for IN clauses, so direct interpolation
        # is used here; safety is guaranteed by the regex above.
        ids_literal = ",".join(f"'{i}'" for i in safe_ids)
        soql = (
            "SELECT Id, Name, StageName, Amount, CloseDate, AccountId, Account.Name "
            f"FROM Opportunity WHERE AccountId IN ({ids_literal}) "
            "ORDER BY CloseDate DESC LIMIT 50"
        )
        return list(sf.query(soql).get("records", []))


def _has_searchable_input(entities: Entities) -> bool:
    return bool(
        entities.get("customer_name")
        or entities.get("contact_name")
        or entities.get("keywords")
    )


def _stage_distribution(opps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = defaultdict(int)
    amounts: dict[str, float] = defaultdict(float)
    for o in opps:
        stage = o.get("StageName") or "Unknown"
        counts[stage] += 1
        amounts[stage] += float(o.get("Amount") or 0.0)
    return [
        {"stage": stage, "count": counts[stage], "amount": amounts[stage]}
        for stage in counts
    ]


def _amount_timeline(opps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[str, float] = defaultdict(float)
    for o in opps:
        date = o.get("CloseDate")
        amt = o.get("Amount")
        if not date or amt is None:
            continue
        by_date[date] += float(amt)
    return [
        {"date": d, "amount": by_date[d]}
        for d in sorted(by_date.keys())
    ]
