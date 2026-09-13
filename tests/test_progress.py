from __future__ import annotations

import json

import pytest

from limpet.report.progress import recompute_snapshot, regenerate
from limpet.store import db

MATCH_A = {
    "match_id": 1,
    "account_id": 111,
    "played_at": 1_726_000_000,  # a fixed instant; both matches land on the same local day
    "hero_id": 1,
    "won": 1,
    "abandoned": 0,
    "duration_s": 1800,
    "match_mode": 4,
    "kills": 5,
    "deaths": 3,
    "assists": 10,
    "net_worth": 20000,
    "average_badge": 60,
    "raw_meta_path": None,
    "demo_path": None,
    "ingested_at": None,
    "analyzed_at": None,
}


def _report(micro_rating: int, macro_rating: int) -> dict:
    return {
        "summary": "x",
        "micro": {"assessment": "x", "rating": micro_rating, "strengths": [], "mistakes": []},
        "macro": {"assessment": "x", "rating": macro_rating, "strengths": [], "mistakes": []},
        "focus_this_week": [{"dimension": "micro", "theme": "x", "why": "x", "drill": "x"}],
        "progress_note": "x",
    }


@pytest.fixture
def conn(data_dir):
    with db.session() as c:
        yield c


def _seed_match_and_report(conn, match_id, played_at, micro, macro, percentiles=()):
    row = {**MATCH_A, "match_id": match_id, "played_at": played_at}
    db.upsert_match(conn, row)
    features = {"micro": {}, "macro": {}}
    if percentiles:
        features["micro"]["aim"] = {
            f"leaf{i}": {"value": 1, "unit": "", "benchmark_percentile": p}
            for i, p in enumerate(percentiles)
        }
    db.save_features(conn, match_id, features, None, played_at)
    db.save_report(
        conn, match_id, "claude-opus-5", f"# report {match_id}", _report(micro, macro), played_at
    )


def test_regenerate_with_no_data_is_still_valid_markdown(conn):
    md = regenerate(conn)
    assert "# Deadlock Progress" in md
    assert "None yet — still building your baseline." in md
    assert "no data yet" in md


def test_recompute_snapshot_averages_same_day_matches(conn):
    day_ts = 1_726_000_000
    _seed_match_and_report(conn, 1, day_ts, micro=60, macro=40, percentiles=[50, 70])
    _seed_match_and_report(conn, 2, day_ts + 60, micro=80, macro=60, percentiles=[30])

    import datetime as _dt

    day = _dt.datetime.fromtimestamp(day_ts).strftime("%Y-%m-%d")
    recompute_snapshot(conn, day=day)

    snap = db.recent_snapshots(conn, limit=1)[0]
    metrics = json.loads(snap["metrics_json"])
    assert metrics["matches_analyzed"] == 2
    assert metrics["micro_rating_avg"] == 70.0  # (60 + 80) / 2
    assert metrics["macro_rating_avg"] == 50.0  # (40 + 60) / 2
    assert metrics["benchmark_percentile_avg"] == pytest.approx(50.0)  # (50+70+30)/3


def test_recompute_snapshot_no_op_for_a_day_with_no_matches(conn):
    recompute_snapshot(conn, day="1999-01-01")
    assert db.recent_snapshots(conn) == []


def test_regenerate_renders_focus_areas_and_trend(conn):
    _seed_match_and_report(conn, 1, 1_726_000_000, micro=60, macro=40)
    db.open_focus_area(conn, match_id=1, dimension="micro", theme="Missed shots", now=1_726_000_000)
    resolved_id = db.open_focus_area(
        conn, match_id=1, dimension="macro", theme="Bad rotations", now=1_726_000_000
    )
    db.update_focus_area_status(conn, resolved_id, "resolved", match_id=1, now=1_726_000_100)

    # regenerate() only recomputes *today's* snapshot (the day something could have
    # changed) — precompute the seeded (historical) day explicitly, same as if this
    # test were running "on" 2024-09-11.
    import datetime as _dt

    recompute_snapshot(conn, day=_dt.datetime.fromtimestamp(1_726_000_000).strftime("%Y-%m-%d"))
    md = regenerate(conn)

    assert "[micro] Missed shots" in md
    assert "active" in md
    assert "## Recently resolved" in md
    assert "[macro] Bad rotations" in md
    assert "## Trend" in md
    assert "60.0" in md and "40.0" in md
