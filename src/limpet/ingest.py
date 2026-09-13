"""Ingestion: pull match history and raw match metadata, persist to disk + DB."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from . import paths
from .api.client import DeadlockClient
from .api.models import MatchHistoryEntry
from .parse.metadata import load as load_match
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


def cached_metadata(match_id: int) -> dict | None:
    """Read previously-fetched metadata from disk without hitting the API."""
    path = paths.match_cache_dir(match_id) / "metadata.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


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


def upsert_from_metadata(conn: sqlite3.Connection, meta: dict, account_id: int) -> None:
    """Upsert a `matches` row derived straight from full metadata.

    Unlike `_history_row` (from `/match-history`), this doesn't need a prior
    `sync` — `analyze` can run standalone on any match id. Raises
    `PlayerNotInMatch` if `account_id` didn't play in this match. Leaves
    `raw_meta_path`/`ingested_at`/`demo_path`/`analyzed_at` untouched by the
    caller's own follow-up updates (this only sets scoreboard-derived fields).
    """
    view = load_match(meta)
    player = view.player(account_id)  # raises PlayerNotInMatch
    won = view.winning_team is not None and player.get("team") == view.winning_team
    db.upsert_match(
        conn,
        {
            "match_id": view.match_id,
            "account_id": account_id,
            "played_at": view.raw.get("start_time", 0),
            "hero_id": player.get("hero_id", 0),
            "won": int(won),
            "abandoned": int(bool(player.get("abandon_match_time_s"))),
            "duration_s": view.duration_s,
            "match_mode": view.raw.get("match_mode", 0),
            "kills": player.get("kills"),
            "deaths": player.get("deaths"),
            "assists": player.get("assists"),
            "net_worth": player.get("net_worth"),
            "average_badge": view.average_badge(account_id),
            "raw_meta_path": None,
            "demo_path": None,
            "ingested_at": None,
            "analyzed_at": None,
        },
    )
