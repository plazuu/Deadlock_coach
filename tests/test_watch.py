from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from limpet import paths
from limpet.api.client import DeadlockAPIError, MetadataNotReady
from limpet.cli import MAX_OTHER_ATTEMPTS, _run_watch_cycle
from limpet.config import Settings
from limpet.store import db

FIXTURES = Path(__file__).parent / "fixtures"
ACCOUNT_ID = 173907991
MATCH_ID = 104887482

VALID_REPORT = {
    "summary": "Solid game.",
    "micro": {"assessment": "fine", "rating": 60, "strengths": ["aim"], "mistakes": []},
    "macro": {"assessment": "fine", "rating": 55, "strengths": ["objectives"], "mistakes": []},
    "focus_this_week": [
        {"dimension": "micro", "theme": "aim", "why": "low accuracy", "drill": "aim trainer"}
    ],
    "progress_note": "first tracked match",
}


def test_enqueue_watch_is_idempotent(data_dir):
    with db.session() as conn:
        db.enqueue_watch(conn, 111, 1000)
        db.enqueue_watch(conn, 111, 2000)  # must not reset the existing row
        rows = conn.execute("SELECT * FROM watch_queue").fetchall()
        assert len(rows) == 1
        assert rows[0]["next_attempt_at"] == 1000
        assert rows[0]["attempts"] == 0


def test_due_watch_items_respects_next_attempt_at(data_dir):
    with db.session() as conn:
        db.enqueue_watch(conn, 111, 1000)  # due now
        db.enqueue_watch(conn, 222, 1000)
        db.reschedule_watch(conn, 222, next_attempt_at=5000, error="not ready", now=1000)

    with db.session() as conn:
        due = db.due_watch_items(conn, now=2000)
        assert [r["match_id"] for r in due] == [111]

        due_later = db.due_watch_items(conn, now=6000)
        assert {r["match_id"] for r in due_later} == {111, 222}


def test_due_watch_items_orders_oldest_enqueued_first(data_dir):
    with db.session() as conn:
        db.enqueue_watch(conn, 222, 2000)
        db.enqueue_watch(conn, 111, 1000)

    with db.session() as conn:
        due = db.due_watch_items(conn, now=5000)
        assert [r["match_id"] for r in due] == [111, 222]


def test_reschedule_bumps_attempts_and_stays_pending(data_dir):
    with db.session() as conn:
        db.enqueue_watch(conn, 111, 1000)
        db.reschedule_watch(conn, 111, next_attempt_at=2000, error="still not ready", now=1500)

    with db.session() as conn:
        row = conn.execute("SELECT * FROM watch_queue WHERE match_id = 111").fetchone()
        assert row["status"] == "pending"
        assert row["attempts"] == 1
        assert row["last_error"] == "still not ready"
        assert row["next_attempt_at"] == 2000


def test_mark_watch_done(data_dir):
    with db.session() as conn:
        db.enqueue_watch(conn, 111, 1000)
        db.mark_watch_done(conn, 111, now=1500)

    with db.session() as conn:
        row = conn.execute("SELECT * FROM watch_queue WHERE match_id = 111").fetchone()
        assert row["status"] == "done"
        assert db.due_watch_items(conn, now=9999) == []


def test_fail_watch_is_terminal(data_dir):
    with db.session() as conn:
        db.enqueue_watch(conn, 111, 1000)
        db.fail_watch(conn, 111, error="gave up", now=1500)

    with db.session() as conn:
        row = conn.execute("SELECT * FROM watch_queue WHERE match_id = 111").fetchone()
        assert row["status"] == "failed"
        assert row["last_error"] == "gave up"
        assert db.due_watch_items(conn, now=9999) == []


def test_notify_no_ops_when_no_notifier_available(monkeypatch):
    from limpet import notify as notify_module

    monkeypatch.setattr(notify_module.platform, "system", lambda: "Windows")
    calls = []
    monkeypatch.setattr(notify_module.subprocess, "run", lambda *a, **k: calls.append((a, k)))
    notify_module.notify("Limpet", "test")
    assert calls == []


def test_notify_calls_osascript_on_darwin(monkeypatch):
    from limpet import notify as notify_module

    monkeypatch.setattr(notify_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(notify_module.shutil, "which", lambda name: "/usr/bin/osascript")
    calls = []
    monkeypatch.setattr(notify_module.subprocess, "run", lambda *a, **k: calls.append((a, k)))
    notify_module.notify("Limpet", 'weird "quoted" body')
    assert len(calls) == 1
    args = calls[0][0][0]
    assert args[0] == "osascript"
    assert '\\"quoted\\"' in args[2]


def test_notify_never_raises_even_if_subprocess_fails(monkeypatch):
    from limpet import notify as notify_module

    monkeypatch.setattr(notify_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(notify_module.shutil, "which", lambda name: "/usr/bin/notify-send")

    def _boom(*a, **k):
        raise OSError("no such binary")

    monkeypatch.setattr(notify_module.subprocess, "run", _boom)
    notify_module.notify("Limpet", "test")  # must not raise


# -- _run_watch_cycle end-to-end (fakes only, no real network/LLM) --------


class FakeAssets:
    def item(self, item_id):
        return None

    def item_name(self, item_id):
        return f"item:{item_id}"

    def hero_name(self, hero_id):
        return f"hero:{hero_id}"

    def rank_name(self, badge):
        return "Archon 3"


def _history_entry(match_id: int, match_mode: int) -> dict:
    return {
        "account_id": ACCOUNT_ID,
        "match_id": match_id,
        "hero_id": 17,
        "hero_level": 20,
        "start_time": 1_700_000_000,
        "game_mode": 1,
        "match_mode": match_mode,
        "player_team": 0,
        "player_kills": 5,
        "player_deaths": 2,
        "player_assists": 7,
        "denies": 3,
        "last_hits": 71,
        "net_worth": 20000,
        "match_duration_s": 1868,
        "match_result": 1,
        "player_match_outcome": 1,
        "objectives_mask_team0": 0,
        "objectives_mask_team1": 0,
    }


class FakeClient:
    def __init__(self, history, *, metadata_error=None):
        self._history = history
        self._metadata_error = metadata_error

    def match_history(self, account_id, *, force_refetch=False):
        return self._history

    def match_metadata(self, match_id):
        if self._metadata_error is not None:
            raise self._metadata_error
        raise AssertionError("cached metadata should have been used in this test")

    def player_stats_metrics(self, **kwargs):
        raise DeadlockAPIError("no benchmarks in test")


class FakeMessages:
    def create(self, **kwargs):
        return SimpleNamespace(
            stop_reason="end_turn",
            stop_details=None,
            content=[SimpleNamespace(type="text", text=json.dumps(VALID_REPORT))],
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )


class FakeAnthropicClient:
    def __init__(self):
        self.messages = FakeMessages()


def _seed_fixture_metadata(match_id: int) -> None:
    out_dir = paths.match_cache_dir(match_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = (FIXTURES / "match_104887482.json").read_text()
    (out_dir / "metadata.json").write_text(raw)


@pytest.fixture
def settings():
    return Settings()


def test_watch_cycle_processes_a_new_ranked_match(data_dir, settings):
    _seed_fixture_metadata(MATCH_ID)
    client = FakeClient([_history_entry(MATCH_ID, match_mode=4)])  # 4 = ranked

    with db.session() as conn:
        _run_watch_cycle(
            client,
            conn,
            ACCOUNT_ID,
            FakeAssets(),
            settings,
            FakeAnthropicClient(),
            stop_requested=lambda: False,
        )

    with db.session() as conn:
        assert db.due_watch_items(conn, now=10**12) == []
        row = conn.execute("SELECT * FROM watch_queue WHERE match_id = ?", (MATCH_ID,)).fetchone()
        assert row["status"] == "done"
        report_row = conn.execute(
            "SELECT * FROM reports WHERE match_id = ?", (MATCH_ID,)
        ).fetchone()
        assert report_row is not None


def test_watch_cycle_skips_matches_outside_configured_modes(data_dir, settings):
    client = FakeClient([_history_entry(MATCH_ID, match_mode=3)])  # 3 = coop_bot

    with db.session() as conn:
        _run_watch_cycle(
            client,
            conn,
            ACCOUNT_ID,
            FakeAssets(),
            settings,
            FakeAnthropicClient(),
            stop_requested=lambda: False,
        )

    with db.session() as conn:
        assert conn.execute("SELECT * FROM watch_queue").fetchall() == []


def test_watch_cycle_reschedules_on_metadata_not_ready(data_dir, settings):
    client = FakeClient(
        [_history_entry(MATCH_ID, match_mode=4)], metadata_error=MetadataNotReady("not ready")
    )

    with db.session() as conn:
        _run_watch_cycle(
            client,
            conn,
            ACCOUNT_ID,
            FakeAssets(),
            settings,
            FakeAnthropicClient(),
            stop_requested=lambda: False,
        )

    with db.session() as conn:
        row = conn.execute("SELECT * FROM watch_queue WHERE match_id = ?", (MATCH_ID,)).fetchone()
        assert row["status"] == "pending"
        assert row["attempts"] == 1
        assert row["next_attempt_at"] > int(time.time())


def test_watch_cycle_fails_after_max_other_attempts(data_dir, settings):
    client = FakeClient(
        [_history_entry(MATCH_ID, match_mode=4)], metadata_error=DeadlockAPIError("boom")
    )

    for _ in range(MAX_OTHER_ATTEMPTS):
        with db.session() as conn:
            # force every retry to be immediately due regardless of backoff
            conn.execute("UPDATE watch_queue SET next_attempt_at = 0")
            _run_watch_cycle(
                client,
                conn,
                ACCOUNT_ID,
                FakeAssets(),
                settings,
                FakeAnthropicClient(),
                stop_requested=lambda: False,
            )

    with db.session() as conn:
        row = conn.execute("SELECT * FROM watch_queue WHERE match_id = ?", (MATCH_ID,)).fetchone()
        assert row["status"] == "failed"
        assert row["attempts"] == MAX_OTHER_ATTEMPTS
