"""Tests for backend.hub backpressure semantics.

Validates the contract: when a client's send queue is full, the oldest
*non-critical* event is dropped to make room. Critical events
(``topic_shift``, ``error``, ``crm_offline``, etc.) must be preserved
even under sustained backpressure.
"""
from __future__ import annotations

import asyncio
import json
import pytest

from backend.hub import _ClientChannel, is_critical, CRITICAL_EVENT_TYPES


class _FakeWebSocket:
    """Minimal stand-in: records sent text but never auto-drains."""

    def __init__(self):
        self.sent: list[str] = []
        self.client_state = None

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)


def _payload(evt: dict) -> str:
    return json.dumps(evt)


def test_critical_event_classifier_covers_required_types():
    # The frontend depends on these specific event types being preserved.
    for t in ("topic_shift", "error", "crm_offline", "crm_online"):
        assert is_critical({"type": t}), f"{t!r} should be critical"
    assert t in CRITICAL_EVENT_TYPES
    # Routine high-volume events must not be critical, otherwise the
    # backpressure policy degenerates and the queue never drains.
    assert not is_critical({"type": "transcript"})
    assert not is_critical({"type": "entities"})


@pytest.mark.asyncio
async def test_full_queue_drops_oldest_noncritical_first():
    """When the queue is full, the oldest non-critical entry is dropped
    to make room — the topic_shift sitting in front of it survives."""
    ws = _FakeWebSocket()
    ch = _ClientChannel(ws, max_depth=3)

    crit_evt = {"type": "topic_shift", "label": "Renewal"}
    t1 = {"type": "transcript", "text": "one"}
    t2 = {"type": "transcript", "text": "two"}
    t3 = {"type": "transcript", "text": "three"}

    assert await ch.offer(crit_evt, _payload(crit_evt))
    assert await ch.offer(t1, _payload(t1))
    assert await ch.offer(t2, _payload(t2))
    # Queue is full; the next non-critical offer must succeed by
    # dropping the OLDEST non-critical entry (t1), not the critical one.
    assert await ch.offer(t3, _payload(t3))

    # Inspect internal queue ordering: critical event first, then t2, t3.
    queued_types = [json.loads(p)["type"] for (p, _) in ch._queue]
    queued_texts = [json.loads(p).get("text") for (p, _) in ch._queue]
    assert queued_types[0] == "topic_shift"
    assert "one" not in queued_texts, "oldest non-critical (t1) should be dropped"
    assert "two" in queued_texts and "three" in queued_texts
    assert ch._dropped_count == 1


@pytest.mark.asyncio
async def test_critical_events_survive_sustained_pressure():
    """Many transcripts after a topic_shift must never evict the shift."""
    ws = _FakeWebSocket()
    ch = _ClientChannel(ws, max_depth=4)

    shift = {"type": "topic_shift", "label": "Q2 Pipeline"}
    err = {"type": "error", "stage": "salesforce", "message": "offline"}
    assert await ch.offer(shift, _payload(shift))
    assert await ch.offer(err, _payload(err))

    # Flood with non-critical chatter.
    for i in range(20):
        evt = {"type": "transcript", "text": f"line {i}"}
        await ch.offer(evt, _payload(evt))

    queued_types = [json.loads(p)["type"] for (p, _) in ch._queue]
    assert "topic_shift" in queued_types
    assert "error" in queued_types
    assert ch._dropped_count > 0
