from __future__ import annotations

from limpet.ingest import sync_match_history
from limpet.store import db


class FakeClient:
    def __init__(self, entries):
        self._entries = entries

    def match_history(self, account_id, *, force_refetch=False):
        return self._entries


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
