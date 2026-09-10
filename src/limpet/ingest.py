"""Ingestion: pull match history and raw match metadata, persist to disk + DB."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from . import paths
from .api.client import DeadlockClient
from .api.models import MatchHistoryEntry
from .store import db


def sync_match_history(
    client: DeadlockClient,
    conn: sqlite3.Connection,
    account_id: int,
    *,
    force_refetch: bool = False,
) -> list[MatchHistoryEntry]:
    """Fetch match history and upsert a lightweight row per match.

    Returns the entries that were not previously in the database, newest first.
    """
    raw = client.match_history(account_id, force_refetch=force_refetch)
    entries = [MatchHistoryEntry.model_validate(r) for r in raw]
    known = db.known_match_ids(conn, account_id)

    new: list[MatchHistoryEntry] = []
    for e in sorted(entries, key=lambda x: x.start_time, reverse=True):
        if e.match_id not in known:
            new.append(e)
        db.upsert_match(conn, _history_row(e))
    conn.commit()
    return new


def _history_row(e: MatchHistoryEntry) -> dict:
    return {
        "match_id": e.match_id,
        "account_id": e.account_id,
        "played_at": e.start_time,
        "hero_id": e.hero_id,
        "won": int(e.won),
        "abandoned": int(e.abandoned),
        "duration_s": e.match_duration_s,
        "match_mode": e.match_mode,
        "kills": e.player_kills,
        "deaths": e.player_deaths,
        "assists": e.player_assists,
        "net_worth": e.net_worth,
        "average_badge": None,
        "raw_meta_path": None,
        "demo_path": None,
        "ingested_at": None,
        "analyzed_at": None,
    }


def fetch_metadata(client: DeadlockClient, match_id: int) -> tuple[dict, Path]:
    """Fetch full match metadata and cache it. Raises ``MetadataNotReady`` if pending."""
    meta = client.match_metadata(match_id)
    out_dir = paths.match_cache_dir(match_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "metadata.json"
    path.write_text(json.dumps(meta))
    return meta, path


def ingest_match(client: DeadlockClient, conn: sqlite3.Connection, match_id: int) -> Path:
    """Fetch + cache metadata for a match and record the path on its DB row."""
    _meta, path = fetch_metadata(client, match_id)
    conn.execute(
        "UPDATE matches SET raw_meta_path = ?, ingested_at = ? WHERE match_id = ?",
        (str(path), int(time.time()), match_id),
    )
    conn.commit()
    return path
