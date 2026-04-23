"""Salesforce REST API client wrapper.

Adds session-refresh, idle-timeout, dynamic stage discovery, and an
``is_online`` health flag to support graceful degradation when the org
is unreachable.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable, TypedDict

from simple_salesforce import Salesforce
from simple_salesforce.exceptions import (
    SalesforceAuthenticationFailed,
    SalesforceError,
    SalesforceExpiredSession,
)

from .entities import Entities

logger = logging.getLogger(__name__)


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
    """Salesforce wrapper with auto-refresh, idle timeout, and stage cache."""

    def __init__(
        self,
        username: str,
        password: str,
        security_token: str,
        domain: str = "login",
        idle_refresh_seconds: int = 30 * 60,
        on_status_change: Callable[[bool, str | None], Awaitable[None]] | None = None,
    ):
        self._creds = dict(
            username=username,
            password=password,
            security_token=security_token,
            domain=domain,
        )
        self._sf: Salesforce | None = None
        self._sf_lock = asyncio.Lock()
        self._last_activity: float = 0.0
        self._idle_refresh_seconds = idle_refresh_seconds
        self._stages: tuple[str, ...] = DEFAULT_STAGE_FALLBACK
        self._is_online: bool = False
        self._on_status_change = on_status_change

    # ── Public observable state ──────────────────────────────────────────

    @property
    def is_online(self) -> bool:
        return self._is_online

    def get_stage_names(self) -> tuple[str, ...]:
        """Return the cached Opportunity.StageName picklist (or the fallback)."""
        return self._stages

    # ── Connection management ────────────────────────────────────────────

    def _connect_sync(self) -> Salesforce:
        logger.info("Connecting to Salesforce as %s", self._creds["username"])
        return Salesforce(**self._creds)

    async def _ensure_session(self, force: bool = False) -> Salesforce | None:
        """Connect if needed; reconnect if idle past the refresh threshold."""
        async with self._sf_lock:
            now = time.monotonic()
            stale = (
                self._last_activity > 0
                and now - self._last_activity > self._idle_refresh_seconds
            )
            if force or self._sf is None or stale:
                if stale and self._sf is not None:
                    logger.info(
                        "Salesforce session idle for >%ds, refreshing proactively.",
                        self._idle_refresh_seconds,
                    )
                try:
                    self._sf = await asyncio.to_thread(self._connect_sync)
                    self._last_activity = now
                    await self._set_online(True)
                    # Refresh dynamic stages whenever we (re)connect.
                    await self._refresh_stages_unlocked()
                except SalesforceAuthenticationFailed as exc:
                    self._sf = None
                    logger.error("Salesforce auth failed: %s", exc)
                    await self._set_online(False, f"auth: {exc}")
                    return None
                except Exception as exc:
                    self._sf = None
                    logger.warning("Salesforce connect failed: %s", exc)
                    await self._set_online(False, str(exc))
                    return None
            return self._sf

    async def _set_online(self, online: bool, reason: str | None = None) -> None:
        if self._is_online == online:
            return
        self._is_online = online
        logger.info("Salesforce status -> %s (%s)", "online" if online else "offline", reason or "")
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
        """Best-effort startup connect — never raises."""
        await self._ensure_session()

    # ── Query API ────────────────────────────────────────────────────────

    async def query_for_entities(self, entities: Entities) -> CrmResult:
        if not _has_searchable_input(entities):
            return _empty_result()

        sf = await self._ensure_session()
        if sf is None:
            return _empty_result()

        try:
            return await asyncio.to_thread(self._query_sync, sf, entities)
        except SalesforceExpiredSession as exc:
            logger.info("Salesforce session expired (%s); re-authenticating once.", exc)
            sf = await self._ensure_session(force=True)
            if sf is None:
                return _empty_result()
            try:
                return await asyncio.to_thread(self._query_sync, sf, entities)
            except SalesforceError as exc2:
                logger.warning("Salesforce query failed after refresh: %s", exc2)
                await self._set_online(False, str(exc2))
                return _empty_result()
        except SalesforceAuthenticationFailed as exc:
            logger.warning("Salesforce auth failed mid-session: %s", exc)
            await self._set_online(False, str(exc))
            return _empty_result()
        except SalesforceError as exc:
            logger.warning("Salesforce query failed: %s", exc)
            await self._set_online(False, str(exc))
            return _empty_result()
        except Exception as exc:
            logger.warning("Unexpected Salesforce error: %s", exc)
            await self._set_online(False, str(exc))
            return _empty_result()
        finally:
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

    @staticmethod
    def _search_accounts(sf: Salesforce, term: str) -> list[dict[str, Any]]:
        escaped = term.replace("'", "\\'")
        soql = (
            "SELECT Id, Name, Industry, Type, Website "
            f"FROM Account WHERE Name LIKE '%{escaped}%' LIMIT 10"
        )
        return list(sf.query(soql).get("records", []))

    @staticmethod
    def _search_opportunities(sf: Salesforce, term: str) -> list[dict[str, Any]]:
        escaped = term.replace("'", "\\'")
        soql = (
            "SELECT Id, Name, StageName, Amount, CloseDate, AccountId, Account.Name "
            f"FROM Opportunity WHERE Name LIKE '%{escaped}%' "
            f"OR Account.Name LIKE '%{escaped}%' "
            "ORDER BY CloseDate DESC LIMIT 25"
        )
        return list(sf.query(soql).get("records", []))

    @staticmethod
    def _opportunities_by_accounts(
        sf: Salesforce, account_ids: set[str]
    ) -> list[dict[str, Any]]:
        ids = ",".join(f"'{i}'" for i in list(account_ids)[:20])
        soql = (
            "SELECT Id, Name, StageName, Amount, CloseDate, AccountId, Account.Name "
            f"FROM Opportunity WHERE AccountId IN ({ids}) "
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
