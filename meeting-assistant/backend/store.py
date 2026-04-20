"""SQLite persistence for meeting sessions, transcripts, entities, and CRM data."""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "meetings.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    session_id REAL NOT NULL,
    label TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS transcript_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id TEXT NOT NULL REFERENCES meetings(id),
    ts REAL NOT NULL,
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meeting_entities (
    meeting_id TEXT PRIMARY KEY REFERENCES meetings(id),
    data TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS meeting_crm (
    meeting_id TEXT PRIMARY KEY REFERENCES meetings(id),
    data TEXT NOT NULL DEFAULT '{}'
);
"""


class MeetingStore:
    """Thread-safe SQLite store for meeting history.

    All public methods are async and safe to await from FastAPI handlers or
    asyncio tasks; the blocking SQLite calls are dispatched via
    ``asyncio.to_thread`` so they never stall the event loop.
    """

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = str(db_path)
        self._init_db()

    # ── internal helpers ─────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ── sync implementations (called via to_thread) ──────────────────────────

    def _create_meeting_sync(
        self,
        meeting_id: str,
        started_at: float,
        session_id: float,
        label: str,
        summary: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO meetings
                   (id, started_at, session_id, label, summary)
                   VALUES (?,?,?,?,?)""",
                (meeting_id, started_at, session_id, label, summary or ""),
            )

    def _append_transcript_sync(self, meeting_id: str, ts: float, text: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO transcript_lines (meeting_id, ts, text) VALUES (?,?,?)",
                (meeting_id, ts, text),
            )

    def _upsert_entities_sync(self, meeting_id: str, data: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO meeting_entities (meeting_id, data) VALUES (?,?)",
                (meeting_id, json.dumps(data)),
            )

    def _upsert_crm_sync(self, meeting_id: str, data: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO meeting_crm (meeting_id, data) VALUES (?,?)",
                (meeting_id, json.dumps(data)),
            )

    def _list_meetings_sync(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, started_at, session_id, label, summary
                   FROM meetings
                   ORDER BY started_at DESC
                   LIMIT 500"""
            ).fetchall()
        return [dict(r) for r in rows]

    def _delete_meeting_sync(self, meeting_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM transcript_lines WHERE meeting_id=?", (meeting_id,))
            conn.execute("DELETE FROM meeting_entities WHERE meeting_id=?", (meeting_id,))
            conn.execute("DELETE FROM meeting_crm WHERE meeting_id=?", (meeting_id,))
            conn.execute("DELETE FROM meetings WHERE id=?", (meeting_id,))

    def _get_meeting_sync(self, meeting_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, started_at, session_id, label, summary FROM meetings WHERE id=?",
                (meeting_id,),
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            lines_rows = conn.execute(
                "SELECT ts, text FROM transcript_lines WHERE meeting_id=? ORDER BY ts",
                (meeting_id,),
            ).fetchall()
            result["lines"] = [{"ts": r["ts"], "text": r["text"]} for r in lines_rows]
            ent_row = conn.execute(
                "SELECT data FROM meeting_entities WHERE meeting_id=?",
                (meeting_id,),
            ).fetchone()
            result["entities"] = json.loads(ent_row["data"]) if ent_row else {}
            crm_row = conn.execute(
                "SELECT data FROM meeting_crm WHERE meeting_id=?",
                (meeting_id,),
            ).fetchone()
            result["crm"] = json.loads(crm_row["data"]) if crm_row else {}
        return result

    # ── async public API ─────────────────────────────────────────────────────

    async def create_meeting(
        self,
        meeting_id: str,
        started_at: float,
        session_id: float,
        label: str,
        summary: str = "",
    ) -> None:
        """Persist a new (or updated) meeting record."""
        await asyncio.to_thread(
            self._create_meeting_sync, meeting_id, started_at, session_id, label, summary
        )

    async def append_transcript(self, meeting_id: str, ts: float, text: str) -> None:
        """Append a single transcript line to a meeting."""
        await asyncio.to_thread(self._append_transcript_sync, meeting_id, ts, text)

    async def upsert_entities(self, meeting_id: str, data: dict[str, Any]) -> None:
        """Overwrite the stored entities for a meeting."""
        await asyncio.to_thread(self._upsert_entities_sync, meeting_id, data)

    async def upsert_crm(self, meeting_id: str, data: dict[str, Any]) -> None:
        """Overwrite the stored CRM data for a meeting."""
        await asyncio.to_thread(self._upsert_crm_sync, meeting_id, data)

    async def list_meetings(self) -> list[dict[str, Any]]:
        """Return meeting summaries (no lines/entities/crm), newest first."""
        return await asyncio.to_thread(self._list_meetings_sync)

    async def get_meeting(self, meeting_id: str) -> dict[str, Any] | None:
        """Return full meeting data including transcript lines, entities, and CRM."""
        return await asyncio.to_thread(self._get_meeting_sync, meeting_id)

    async def delete_meeting(self, meeting_id: str) -> None:
        """Delete a meeting and all its associated data."""
        await asyncio.to_thread(self._delete_meeting_sync, meeting_id)
