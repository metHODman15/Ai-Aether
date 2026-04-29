"""Shared Salesforce data structures and pure-helpers.

The transport layer (``simple_salesforce`` REST) was removed in V6.0.0
when the data layer migrated to the Salesforce Hosted MCP Server. This
module now only owns:

* :class:`CrmResult` — the dashboard-facing result shape.
* :data:`DEFAULT_STAGE_FALLBACK` — used when Opportunity stage metadata
  cannot be retrieved.
* :data:`_SF_ID_RE` — strict 15/18-char validator for Salesforce record
  IDs (used to scrub IN-clause inputs before SOQL composition).
* :func:`_has_searchable_input`, :func:`_stage_distribution`,
  :func:`_amount_timeline` — pure aggregations consumed by both the MCP
  client and the existing dashboard tests.

The actual Salesforce session lives in :mod:`backend.mcp_client`.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, TypedDict

from .entities import Entities

# Conservative fallback used when the live Opportunity.StageName picklist
# cannot be retrieved (e.g. permission issue on a fresh sandbox or the
# MCP describe tool is unavailable).
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

# Salesforce record IDs are exactly 15 or 18 alphanumeric characters.
# Validating before interpolation prevents injection via the IN clause.
_SF_ID_RE = re.compile(r"^[A-Za-z0-9]{15}$|^[A-Za-z0-9]{18}$")


class CrmResult(TypedDict):
    accounts: list[dict[str, Any]]
    opportunities: list[dict[str, Any]]
    stage_distribution: list[dict[str, Any]]
    amount_timeline: list[dict[str, Any]]


def empty_result() -> CrmResult:
    return CrmResult(
        accounts=[], opportunities=[], stage_distribution=[], amount_timeline=[]
    )


# Backwards-compat alias kept private to avoid a surprise rename for any
# remaining importer; new code should call :func:`empty_result`.
_empty_result = empty_result


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
