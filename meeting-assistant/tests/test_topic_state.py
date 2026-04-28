"""Tests for backend.topic_state.TopicState — pure in-memory logic."""
from __future__ import annotations

import pytest

from backend.topic_state import TopicState
from backend.entities import Entities


def _entities(**kwargs) -> Entities:
    base: Entities = Entities(
        customer_name=None,
        contact_name=None,
        deal_amount=None,
        deal_stage=None,
        keywords=[],
    )
    base.update(kwargs)
    return base


class TestMergeEntities:
    def test_merge_non_overlapping_returns_true(self):
        state = TopicState()
        new = _entities(customer_name="Acme", contact_name="Alice", deal_amount=5000.0)
        changed = state.merge_entities(new)
        assert changed is True
        assert state.entities["customer_name"] == "Acme"
        assert state.entities["contact_name"] == "Alice"
        assert state.entities["deal_amount"] == 5000.0

    def test_merge_identical_values_returns_false(self):
        state = TopicState()
        new = _entities(customer_name="Acme")
        state.merge_entities(new)
        changed = state.merge_entities(new)
        assert changed is False

    def test_keywords_append_and_no_duplicates(self):
        state = TopicState()
        state.merge_entities(_entities(keywords=["cloud", "renewal"]))
        state.merge_entities(_entities(keywords=["renewal", "pricing"]))
        kws = state.entities["keywords"]
        assert "cloud" in kws
        assert "renewal" in kws
        assert "pricing" in kws
        assert kws.count("renewal") == 1

    def test_keywords_capped_at_eight(self):
        state = TopicState()
        many_keywords = [f"kw{i}" for i in range(10)]
        state.merge_entities(_entities(keywords=many_keywords))
        assert len(state.entities["keywords"]) <= 8

    def test_deal_amount_update(self):
        state = TopicState()
        state.merge_entities(_entities(deal_amount=1000.0))
        changed = state.merge_entities(_entities(deal_amount=2000.0))
        assert changed is True
        assert state.entities["deal_amount"] == 2000.0

    def test_deal_stage_update(self):
        state = TopicState()
        state.merge_entities(_entities(deal_stage="Prospecting"))
        changed = state.merge_entities(_entities(deal_stage="Closed Won"))
        assert changed is True
        assert state.entities["deal_stage"] == "Closed Won"


class TestReset:
    def test_reset_clears_entities_and_sets_label(self):
        state = TopicState()
        state.merge_entities(_entities(customer_name="Acme", deal_amount=500.0, keywords=["a"]))
        state.reset(label="New Topic", summary="fresh start", started_at=1.0)
        assert state.label == "New Topic"
        assert state.summary == "fresh start"
        assert state.started_at == 1.0
        assert state.entities["customer_name"] is None
        assert state.entities["deal_amount"] is None
        assert state.entities["keywords"] == []

    def test_reset_then_merge_starts_fresh(self):
        state = TopicState()
        state.merge_entities(_entities(customer_name="OldCo"))
        state.reset(label="Another", summary="", started_at=2.0)
        changed = state.merge_entities(_entities(customer_name="NewCo"))
        assert changed is True
        assert state.entities["customer_name"] == "NewCo"
