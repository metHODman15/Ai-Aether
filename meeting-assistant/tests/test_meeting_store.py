"""Integration tests for MeetingStore (SQLite persistence)."""
import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from backend.store import MeetingStore


@pytest.fixture
def tmp_store(tmp_path):
    """Return a MeetingStore backed by a temporary database file."""
    db = tmp_path / "test_meetings.db"
    return MeetingStore(db)


def run(coro):
    """Run a coroutine synchronously (compatible with pytest without asyncio plugin)."""
    return asyncio.get_event_loop().run_until_complete(coro)


def test_create_and_list(tmp_store):
    run(tmp_store.create_meeting("m1", 1000.0, 900.0, "Q4 Review", "Summary"))
    meetings = run(tmp_store.list_meetings())
    assert len(meetings) == 1
    assert meetings[0]["id"] == "m1"
    assert meetings[0]["label"] == "Q4 Review"
    assert meetings[0]["session_id"] == 900.0


def test_append_transcript(tmp_store):
    run(tmp_store.create_meeting("m2", 2000.0, 1900.0, "Pipeline Review"))
    run(tmp_store.append_transcript("m2", 2001.0, "Hello world"))
    run(tmp_store.append_transcript("m2", 2002.0, "Second line"))
    meeting = run(tmp_store.get_meeting("m2"))
    assert meeting is not None
    assert len(meeting["lines"]) == 2
    assert meeting["lines"][0]["text"] == "Hello world"
    assert meeting["lines"][1]["text"] == "Second line"


def test_upsert_entities(tmp_store):
    run(tmp_store.create_meeting("m3", 3000.0, 2900.0, "Deal Sync"))
    run(tmp_store.upsert_entities("m3", {"customer_name": "Acme", "keywords": ["q4", "renewal"]}))
    meeting = run(tmp_store.get_meeting("m3"))
    assert meeting["entities"]["customer_name"] == "Acme"
    assert "q4" in meeting["entities"]["keywords"]
    run(tmp_store.upsert_entities("m3", {"customer_name": "Globex"}))
    meeting = run(tmp_store.get_meeting("m3"))
    assert meeting["entities"]["customer_name"] == "Globex"


def test_upsert_crm(tmp_store):
    run(tmp_store.create_meeting("m4", 4000.0, 3900.0, "Intro Call"))
    crm = {"accounts": [{"Name": "Acme Corp"}], "opportunities": [], "stage_distribution": []}
    run(tmp_store.upsert_crm("m4", crm))
    meeting = run(tmp_store.get_meeting("m4"))
    assert meeting["crm"]["accounts"][0]["Name"] == "Acme Corp"


def test_delete_meeting(tmp_store):
    run(tmp_store.create_meeting("m5", 5000.0, 4900.0, "To Delete"))
    run(tmp_store.append_transcript("m5", 5001.0, "Some text"))
    run(tmp_store.upsert_entities("m5", {"customer_name": "Acme"}))
    run(tmp_store.upsert_crm("m5", {"accounts": []}))
    run(tmp_store.delete_meeting("m5"))
    meetings = run(tmp_store.list_meetings())
    assert all(m["id"] != "m5" for m in meetings)
    assert run(tmp_store.get_meeting("m5")) is None


def test_get_missing_meeting(tmp_store):
    result = run(tmp_store.get_meeting("nonexistent"))
    assert result is None


def test_list_multiple_newest_first(tmp_store):
    run(tmp_store.create_meeting("older", 1000.0, 900.0, "Old Meeting"))
    run(tmp_store.create_meeting("newer", 2000.0, 1900.0, "New Meeting"))
    meetings = run(tmp_store.list_meetings())
    assert meetings[0]["id"] == "newer"
    assert meetings[1]["id"] == "older"
