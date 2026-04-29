"""Tests for the pure helpers exported by backend.salesforce_client.

The transport layer (``simple_salesforce`` REST) was removed in V6.0.0
when the data layer migrated to the Salesforce Hosted MCP Server.
Only the pure-helper surface lives here now:

* ``_has_searchable_input``
* ``_stage_distribution``
* ``_amount_timeline``
* ``empty_result`` / ``CrmResult`` shape

The MCP transport tests live in ``test_mcp_client.py``.
"""
from __future__ import annotations

import pytest

from backend.entities import Entities
from backend.salesforce_client import (
    CrmResult,
    DEFAULT_STAGE_FALLBACK,
    _amount_timeline,
    _has_searchable_input,
    _stage_distribution,
    empty_result,
)


def _entities(**kwargs) -> Entities:
    base = Entities(
        customer_name=None, contact_name=None,
        deal_amount=None, deal_stage=None, keywords=[],
    )
    base.update(kwargs)
    return base


# ── _has_searchable_input ─────────────────────────────────────────────────────

class TestHasSearchableInput:
    def test_all_null_returns_false(self):
        assert not _has_searchable_input(_entities())

    def test_customer_name_is_searchable(self):
        assert _has_searchable_input(_entities(customer_name="Acme"))

    def test_contact_name_is_searchable(self):
        assert _has_searchable_input(_entities(contact_name="Jane"))

    def test_keywords_is_searchable(self):
        assert _has_searchable_input(_entities(keywords=["cloud"]))

    def test_empty_keywords_list_is_not_searchable(self):
        assert not _has_searchable_input(_entities(keywords=[]))


# ── _stage_distribution ──────────────────────────────────────────────────────

class TestStageDistribution:
    def test_empty_input(self):
        assert _stage_distribution([]) == []

    def test_single_stage(self):
        opps = [{"StageName": "Prospecting", "Amount": 100}]
        result = _stage_distribution(opps)
        assert len(result) == 1
        assert result[0] == {"stage": "Prospecting", "count": 1, "amount": 100.0}

    def test_multiple_stages_aggregate(self):
        opps = [
            {"StageName": "Prospecting", "Amount": 100},
            {"StageName": "Prospecting", "Amount": 50},
            {"StageName": "Closed Won", "Amount": 200},
        ]
        result = {r["stage"]: r for r in _stage_distribution(opps)}
        assert result["Prospecting"] == {"stage": "Prospecting", "count": 2, "amount": 150.0}
        assert result["Closed Won"] == {"stage": "Closed Won", "count": 1, "amount": 200.0}

    def test_missing_stage_becomes_unknown(self):
        opps = [{"Amount": 100}, {"StageName": None, "Amount": 50}]
        result = _stage_distribution(opps)
        assert any(r["stage"] == "Unknown" for r in result)

    def test_missing_amount_treated_as_zero(self):
        opps = [{"StageName": "Closed Won"}, {"StageName": "Closed Won", "Amount": None}]
        result = _stage_distribution(opps)
        assert result[0]["amount"] == 0.0
        assert result[0]["count"] == 2


# ── _amount_timeline ─────────────────────────────────────────────────────────

class TestAmountTimeline:
    def test_empty_input(self):
        assert _amount_timeline([]) == []

    def test_dates_aggregate_and_sort_ascending(self):
        opps = [
            {"CloseDate": "2025-03-01", "Amount": 100},
            {"CloseDate": "2025-01-15", "Amount": 200},
            {"CloseDate": "2025-01-15", "Amount": 50},
        ]
        result = _amount_timeline(opps)
        assert [r["date"] for r in result] == ["2025-01-15", "2025-03-01"]
        assert result[0]["amount"] == 250.0
        assert result[1]["amount"] == 100.0

    def test_missing_date_skipped(self):
        opps = [{"Amount": 100}, {"CloseDate": None, "Amount": 50}]
        assert _amount_timeline(opps) == []

    def test_missing_amount_skipped(self):
        opps = [{"CloseDate": "2025-01-01"}, {"CloseDate": "2025-01-02", "Amount": None}]
        assert _amount_timeline(opps) == []


# ── empty_result + CrmResult shape ────────────────────────────────────────────

def test_empty_result_shape():
    r = empty_result()
    assert r == {
        "accounts": [],
        "opportunities": [],
        "stage_distribution": [],
        "amount_timeline": [],
    }
    # Mutating one instance must not bleed into another.
    r["accounts"].append({"x": 1})
    assert empty_result()["accounts"] == []


def test_default_stage_fallback_includes_common_stages():
    assert "Closed Won" in DEFAULT_STAGE_FALLBACK
    assert "Prospecting" in DEFAULT_STAGE_FALLBACK


def test_crm_result_typed_dict_assignment_compatible():
    """CrmResult is a TypedDict — assigning a regular dict literal must
    type-check at runtime as well as static-check."""
    r: CrmResult = {
        "accounts": [{"Id": "001"}],
        "opportunities": [],
        "stage_distribution": [],
        "amount_timeline": [],
    }
    assert r["accounts"][0]["Id"] == "001"
