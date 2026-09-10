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

SCHEMA_VERSION = 1

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
