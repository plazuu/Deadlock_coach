"""Regenerate `PROGRESS.md` — the living coaching profile — from SQLite.

Per `docs/PLAN.md` §5a: SQLite (`focus_areas`, `progress_snapshots`) is the
only thing ever written to; this file is a full re-render each time, never
hand-edited and never read from programmatically — its job is to be what a
human opens to see "am I getting better," and (via `coach/briefing.py`'s
`active_focus_areas` query) part of what feeds back into the next match.
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from typing import Any

from .. import paths
from ..store import db


def _day_str(unix_ts: int) -> str:
    return _dt.datetime.fromtimestamp(unix_ts).strftime("%Y-%m-%d")


def recompute_snapshot(conn: sqlite3.Connection, day: str | None = None) -> None:
    """Recompute one day's rolling metrics from the reports/features already
    on file for matches played that day. Idempotent — safe to call every time
    `analyze` finishes; only ever touches the day given (today, by default)."""
    target_day = day or _dt.datetime.now().strftime("%Y-%m-%d")
    day_rows = [r for r in db.reports_with_context(conn) if _day_str(r["played_at"]) == target_day]
    if not day_rows:
        return

    micro_ratings: list[float] = []
    macro_ratings: list[float] = []
    percentiles: list[float] = []
    for row in day_rows:
        report = json.loads(row["structured_json"])
        micro_ratings.append(report["micro"]["rating"])
        macro_ratings.append(report["macro"]["rating"])
        features = json.loads(row["features_json"]) if row["features_json"] else {}
        for dimension in ("micro", "macro"):
            for leaves in features.get(dimension, {}).values():
                for leaf in leaves.values():
                    if "benchmark_percentile" in leaf:
                        percentiles.append(leaf["benchmark_percentile"])

    metrics: dict[str, Any] = {
        "matches_analyzed": len(day_rows),
        "micro_rating_avg": round(sum(micro_ratings) / len(micro_ratings), 1),
        "macro_rating_avg": round(sum(macro_ratings) / len(macro_ratings), 1),
        "benchmark_percentile_avg": (
            round(sum(percentiles) / len(percentiles), 1) if percentiles else None
        ),
    }
    db.upsert_progress_snapshot(conn, target_day, metrics)


def _render_focus_section(title: str, rows: list[sqlite3.Row], empty_note: str) -> list[str]:
    lines = [f"## {title}", ""]
    if not rows:
        lines.append(f"_{empty_note}_")
    for r in rows:
        evidence = json.loads(r["evidence_match_ids"])
        lines.append(
            f"- **[{r['dimension']}] {r['theme']}** — {r['status']}, "
            f"seen in {len(evidence)} game(s) since match {r['first_seen_match']}"
        )
    lines.append("")
    return lines


def _render_solid(latest: sqlite3.Row | None) -> list[str]:
    lines = ["## Solid, as of the last game", ""]
    if latest is None:
        lines.append("_No reports yet._")
        lines.append("")
        return lines
    report = json.loads(latest["structured_json"])
    strengths = report["micro"]["strengths"] + report["macro"]["strengths"]
    if strengths:
        lines += [f"- {s}" for s in strengths]
    else:
        lines.append("_Nothing flagged as solid last game._")
    lines.append("")
    return lines


def _render_trend(snapshots: list[sqlite3.Row]) -> list[str]:
    lines = [
        "## Trend (daily, most recent first)",
        "",
        "| day | matches | micro avg | macro avg | benchmark %ile avg |",
        "|---|---|---|---|---|",
    ]
    if not snapshots:
        lines.append("| _no data yet_ | | | | |")
    for s in snapshots:
        m = json.loads(s["metrics_json"])
        bench = m.get("benchmark_percentile_avg")
        lines.append(
            f"| {s['day']} | {m['matches_analyzed']} | {m['micro_rating_avg']} | "
            f"{m['macro_rating_avg']} | {bench if bench is not None else '—'} |"
        )
    lines.append("")
    return lines


def regenerate(conn: sqlite3.Connection) -> str:
    """Recompute today's snapshot and rewrite `PROGRESS.md` in full."""
    recompute_snapshot(conn)

    focus_rows = db.all_focus_areas(conn)
    active = [r for r in focus_rows if r["status"] in ("active", "improving")]
    resolved = [r for r in focus_rows if r["status"] == "resolved"][:5]

    lines = ["# Deadlock Progress", ""]
    lines += _render_focus_section(
        "Active focus", active, "None yet — still building your baseline."
    )
    lines += _render_focus_section("Recently resolved", resolved, "Nothing resolved yet.")
    lines += _render_solid(db.latest_report(conn))
    lines += _render_trend(db.recent_snapshots(conn))

    markdown = "\n".join(lines)
    paths.progress_path().parent.mkdir(parents=True, exist_ok=True)
    paths.progress_path().write_text(markdown)
    return markdown
