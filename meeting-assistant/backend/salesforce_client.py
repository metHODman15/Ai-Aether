"""Salesforce REST API client wrapper.

Uses simple-salesforce for authentication and SOQL queries. Exposes
helpers that turn extracted entities into Account / Opportunity lookups
plus aggregated metrics for charting.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, TypedDict

from simple_salesforce import Salesforce
from simple_salesforce.exceptions import SalesforceError

from .entities import Entities

logger = logging.getLogger(__name__)


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
    def __init__(
        self,
        username: str,
        password: str,
        security_token: str,
        domain: str = "login",
    ):
        self._creds = dict(
            username=username,
            password=password,
            security_token=security_token,
            domain=domain,
        )
        self._sf: Salesforce | None = None

    def _connect(self) -> Salesforce:
        if self._sf is None:
            logger.info("Connecting to Salesforce as %s", self._creds["username"])
            self._sf = Salesforce(**self._creds)
        return self._sf

    async def query_for_entities(self, entities: Entities) -> CrmResult:
        if not _has_searchable_input(entities):
            return _empty_result()
        return await asyncio.to_thread(self._query_sync, entities)

    def _query_sync(self, entities: Entities) -> CrmResult:
        try:
            sf = self._connect()
        except SalesforceError as exc:
            logger.error("Salesforce auth failed: %s", exc)
            return _empty_result()

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
            try:
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
            except SalesforceError as exc:
                logger.warning("Salesforce query for '%s' failed: %s", term, exc)

        if seen_account_ids:
            try:
                opps = self._opportunities_by_accounts(sf, seen_account_ids)
                for o in opps:
                    if o["Id"] not in seen_opp_ids:
                        seen_opp_ids.add(o["Id"])
                        opportunities.append(o)
            except SalesforceError as exc:
                logger.warning("Opportunity-by-account query failed: %s", exc)

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
