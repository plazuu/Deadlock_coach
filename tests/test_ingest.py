from __future__ import annotations

from limpet.ingest import sync_match_history
from limpet.store import db


class FakeClient:
    def __init__(self, entries):
        self._entries = entries

    def match_history(self, account_id, *, force_refetch=False):
        return self._entries


def test_top_heroes_orders_by_games_played(data_dir):
    def row(match_id: int, hero_id: int) -> dict:
        return {
            "match_id": match_id,
            "account_id": 111,
            "played_at": match_id,
            "hero_id": hero_id,
            "won": 1,
            "abandoned": 0,
            "duration_s": 1800,
            "match_mode": 4,
            "kills": 0,
            "deaths": 0,
            "assists": 0,
            "net_worth": 0,
            "average_badge": 0,
            "raw_meta_path": None,
            "demo_path": None,
            "ingested_at": None,
            "analyzed_at": None,
        }

    with db.session() as conn:
        for match_id, hero_id in [(1, 15), (2, 15), (3, 20), (4, 15), (5, 20), (6, 30)]:
            db.upsert_match(conn, row(match_id, hero_id))

    with db.session() as conn:
        top = db.top_heroes(conn, 111, limit=2)
        assert [(r["hero_id"], r["games"]) for r in top] == [(15, 3), (20, 2)]


def test_analyzed_match_ids(data_dir):
    import time

    with db.session() as conn:
        for match_id, hero_id in [(1, 15), (2, 15), (3, 20)]:
            db.upsert_match(
                conn,
                {
                    "match_id": match_id,
                    "account_id": 111,
                    "played_at": match_id,
                    "hero_id": hero_id,
                    "won": 1,
                    "abandoned": 0,
                    "duration_s": 1800,
                    "match_mode": 4,
                    "kills": 0,
                    "deaths": 0,
                    "assists": 0,
                    "net_worth": 0,
                    "average_badge": 0,
                    "raw_meta_path": None,
                    "demo_path": None,
                    "ingested_at": None,
                    "analyzed_at": None,
                },
            )
        db.save_report(conn, 1, "claude-opus-5", "# md", {"a": 1}, int(time.time()))

    with db.session() as conn:
        assert db.analyzed_match_ids(conn, [1, 2, 3]) == {1}
        assert db.analyzed_match_ids(conn, []) == set()
        assert db.analyzed_match_ids(conn, [2, 3]) == set()


def test_sync_reports_only_new_matches(data_dir, fake_history_entry):
    e2 = {**fake_history_entry, "match_id": 900002, "start_time": 1_726_100_000}
    client = FakeClient([fake_history_entry, e2])

    with db.session() as conn:
        new = sync_match_history(client, conn, account_id=111)
        assert {m.match_id for m in new} == {900001, 900002}
        # newest first
        assert new[0].match_id == 900002

    with db.session() as conn:
        assert db.known_match_ids(conn, 111) == {900001, 900002}
        # second sync: nothing new
        new = sync_match_history(client, conn, account_id=111)
        assert new == []


def test_sync_persists_history_fields(data_dir, fake_history_entry):
    client = FakeClient([fake_history_entry])
    with db.session() as conn:
        sync_match_history(client, conn, account_id=111)
        row = conn.execute("SELECT * FROM matches WHERE match_id = 900001").fetchone()
    assert row["won"] == 1
    assert row["hero_id"] == 15
    assert row["deaths"] == 4
    assert row["duration_s"] == 2100
