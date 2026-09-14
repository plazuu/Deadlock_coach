"""SQLite persistence.

The database is an index and a workflow tracker, not the source of truth — raw
API payloads always land on disk under ``cache/`` and can re-derive every
computed table. Schema is created lazily and migrated by additive ``PRAGMA
user_version`` steps.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .. import paths

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id        INTEGER PRIMARY KEY,
    account_id      INTEGER NOT NULL,
    played_at       INTEGER NOT NULL,          -- unix seconds
    hero_id         INTEGER NOT NULL,
    won             INTEGER NOT NULL,           -- 0/1
    abandoned       INTEGER NOT NULL DEFAULT 0,
    duration_s      INTEGER NOT NULL,
    match_mode      INTEGER NOT NULL,
    kills           INTEGER, deaths INTEGER, assists INTEGER,
    net_worth       INTEGER,
    average_badge   INTEGER,                    -- our rank at the time, 0-116
    raw_meta_path   TEXT,
    demo_path       TEXT,
    ingested_at     INTEGER,
    analyzed_at     INTEGER
);

CREATE TABLE IF NOT EXISTS match_features (
    match_id        INTEGER PRIMARY KEY REFERENCES matches(match_id),
    features_json   TEXT NOT NULL,
    benchmarks_json TEXT,
    computed_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id        INTEGER NOT NULL REFERENCES matches(match_id),
    model           TEXT NOT NULL,
    created_at      INTEGER NOT NULL,
    markdown        TEXT NOT NULL,
    structured_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS focus_areas (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    dimension          TEXT NOT NULL,                    -- micro|macro
    theme              TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'active',   -- active|improving|resolved
    first_seen_match   INTEGER,
    last_seen_match    INTEGER,
    evidence_match_ids TEXT NOT NULL DEFAULT '[]',
    created_at         INTEGER NOT NULL,
    updated_at         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS progress_snapshots (
    day             TEXT PRIMARY KEY,           -- YYYY-MM-DD
    metrics_json    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_matches_played_at ON matches(played_at);
"""

_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS watch_queue (
    match_id        INTEGER PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|done|failed
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER NOT NULL,
    last_error      TEXT,
    enqueued_at     INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_file = path or paths.db_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        conn.executescript(_SCHEMA)
    if version < 2:
        conn.executescript(_SCHEMA_V2)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


@contextmanager
def session(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# -- match helpers -------------------------------------------------------


def upsert_match(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    cols = [
        "match_id",
        "account_id",
        "played_at",
        "hero_id",
        "won",
        "abandoned",
        "duration_s",
        "match_mode",
        "kills",
        "deaths",
        "assists",
        "net_worth",
        "average_badge",
        "raw_meta_path",
        "demo_path",
        "ingested_at",
        "analyzed_at",
    ]
    placeholders = ", ".join(f":{c}" for c in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "match_id")
    conn.execute(
        f"INSERT INTO matches ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(match_id) DO UPDATE SET {updates}",
        {c: row.get(c) for c in cols},
    )


def known_match_ids(conn: sqlite3.Connection, account_id: int) -> set[int]:
    rows = conn.execute(
        "SELECT match_id FROM matches WHERE account_id = ?", (account_id,)
    ).fetchall()
    return {r["match_id"] for r in rows}


def recent_matches(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM matches ORDER BY played_at DESC LIMIT ?", (limit,)
    ).fetchall()


def analyzed_match_ids(conn: sqlite3.Connection, match_ids: list[int]) -> set[int]:
    """Which of `match_ids` already have a stored report."""
    if not match_ids:
        return set()
    placeholders = ", ".join("?" for _ in match_ids)
    rows = conn.execute(
        f"SELECT DISTINCT match_id FROM reports WHERE match_id IN ({placeholders})", match_ids
    ).fetchall()
    return {r["match_id"] for r in rows}


def top_heroes(conn: sqlite3.Connection, account_id: int, limit: int = 3) -> list[sqlite3.Row]:
    """Most-played hero_ids for this account, from locally synced matches."""
    return conn.execute(
        "SELECT hero_id, COUNT(*) AS games FROM matches WHERE account_id = ? "
        "GROUP BY hero_id ORDER BY games DESC LIMIT ?",
        (account_id, limit),
    ).fetchall()


def save_features(
    conn: sqlite3.Connection,
    match_id: int,
    features: dict[str, Any],
    benchmarks: dict[str, Any] | None,
    computed_at: int,
) -> None:
    conn.execute(
        "INSERT INTO match_features (match_id, features_json, benchmarks_json, computed_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(match_id) DO UPDATE SET "
        "features_json=excluded.features_json, benchmarks_json=excluded.benchmarks_json, "
        "computed_at=excluded.computed_at",
        (
            match_id,
            json.dumps(features),
            json.dumps(benchmarks) if benchmarks else None,
            computed_at,
        ),
    )


def save_report(
    conn: sqlite3.Connection,
    match_id: int,
    model: str,
    markdown: str,
    structured: dict[str, Any],
    created_at: int,
) -> None:
    conn.execute(
        "INSERT INTO reports (match_id, model, created_at, markdown, structured_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (match_id, model, created_at, markdown, json.dumps(structured)),
    )


def active_focus_areas(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    """Focus areas still being worked on, most recently touched first."""
    return conn.execute(
        "SELECT * FROM focus_areas WHERE status IN ('active', 'improving') "
        "ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()


def all_focus_areas(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM focus_areas ORDER BY updated_at DESC").fetchall()


def open_focus_area(
    conn: sqlite3.Connection, match_id: int, dimension: str, theme: str, now: int
) -> int:
    """Start tracking a new theme. Returns the new row's id."""
    cur = conn.execute(
        "INSERT INTO focus_areas "
        "(dimension, theme, status, first_seen_match, last_seen_match, "
        " evidence_match_ids, created_at, updated_at) "
        "VALUES (?, ?, 'active', ?, ?, ?, ?, ?)",
        (dimension, theme, match_id, match_id, json.dumps([match_id]), now, now),
    )
    return cur.lastrowid


def update_focus_area_status(
    conn: sqlite3.Connection, focus_area_id: int, status: str, match_id: int, now: int
) -> None:
    """Advance a tracked focus area's status and append this match as evidence."""
    row = conn.execute(
        "SELECT evidence_match_ids FROM focus_areas WHERE id = ?", (focus_area_id,)
    ).fetchone()
    if row is None:
        return
    evidence = json.loads(row["evidence_match_ids"])
    if match_id not in evidence:
        evidence.append(match_id)
    conn.execute(
        "UPDATE focus_areas SET status = ?, last_seen_match = ?, "
        "evidence_match_ids = ?, updated_at = ? WHERE id = ?",
        (status, match_id, json.dumps(evidence), now, focus_area_id),
    )


def upsert_progress_snapshot(conn: sqlite3.Connection, day: str, metrics: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO progress_snapshots (day, metrics_json) VALUES (?, ?) "
        "ON CONFLICT(day) DO UPDATE SET metrics_json = excluded.metrics_json",
        (day, json.dumps(metrics)),
    )


def recent_snapshots(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM progress_snapshots ORDER BY day DESC LIMIT ?", (limit,)
    ).fetchall()


def reports_with_context(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The latest report per match, joined with its play time and features —
    the raw material `report/progress.py` groups by day to build snapshots."""
    return conn.execute(
        "SELECT r.match_id, r.structured_json, m.played_at, f.features_json "
        "FROM reports r "
        "JOIN matches m ON m.match_id = r.match_id "
        "LEFT JOIN match_features f ON f.match_id = r.match_id "
        "WHERE r.id IN (SELECT MAX(id) FROM reports GROUP BY match_id)"
    ).fetchall()


def latest_report(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM reports ORDER BY id DESC LIMIT 1").fetchone()


# -- watch queue -----------------------------------------------------------
# Restart-safe job queue for `limpet watch` (PLAN.md §5, "Auto-watch loop").


def enqueue_watch(conn: sqlite3.Connection, match_id: int, now: int) -> None:
    """Queue a match for the watch pipeline. A no-op if already queued —
    never resets an in-progress row's attempts/status."""
    conn.execute(
        "INSERT OR IGNORE INTO watch_queue "
        "(match_id, status, attempts, next_attempt_at, enqueued_at, updated_at) "
        "VALUES (?, 'pending', 0, ?, ?, ?)",
        (match_id, now, now, now),
    )


def due_watch_items(conn: sqlite3.Connection, now: int) -> list[sqlite3.Row]:
    """Pending queue rows ready to (re)attempt, oldest-enqueued first."""
    return conn.execute(
        "SELECT * FROM watch_queue WHERE status = 'pending' AND next_attempt_at <= ? "
        "ORDER BY enqueued_at ASC",
        (now,),
    ).fetchall()


def mark_watch_done(conn: sqlite3.Connection, match_id: int, now: int) -> None:
    conn.execute(
        "UPDATE watch_queue SET status = 'done', updated_at = ? WHERE match_id = ?",
        (now, match_id),
    )


def reschedule_watch(
    conn: sqlite3.Connection, match_id: int, next_attempt_at: int, error: str, now: int
) -> None:
    """Bump attempts and push the next try out — status stays 'pending'."""
    conn.execute(
        "UPDATE watch_queue SET attempts = attempts + 1, next_attempt_at = ?, "
        "last_error = ?, updated_at = ? WHERE match_id = ?",
        (next_attempt_at, error, now, match_id),
    )


def fail_watch(conn: sqlite3.Connection, match_id: int, error: str, now: int) -> None:
    """Give up on a queued match — terminal, excluded from `due_watch_items`."""
    conn.execute(
        "UPDATE watch_queue SET status = 'failed', attempts = attempts + 1, "
        "last_error = ?, updated_at = ? WHERE match_id = ?",
        (error, now, match_id),
    )
