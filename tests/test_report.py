from __future__ import annotations

from limpet.coach.schema import CoachingReport
from limpet.report.markdown import render
from tests.test_coach import VALID_REPORT


def test_render_includes_both_pillars_and_focus():
    report = CoachingReport.model_validate(VALID_REPORT)
    md = render(
        report,
        match_id=104887482,
        hero_name="Grey Talon",
        won=False,
        played_at=1789066163,
        duration_s=1868,
    )
    assert "# Grey Talon — Loss" in md
    assert "## Micro — 62/100" in md
    assert "## Macro — 48/100" in md
    assert "10:20" in md  # 620s mistake timestamp formatted mm:ss
    assert "## Focus this week" in md
    assert "Solo rotations" in md
    assert "## Progress" in md
    assert report.progress_note in md


def test_render_marks_mental_patterns():
    data = {**VALID_REPORT}
    data["micro"] = {
        **VALID_REPORT["micro"],
        "mistakes": [{**VALID_REPORT["micro"]["mistakes"][0], "mental": True}],
    }
    report = CoachingReport.model_validate(data)
    md = render(report, match_id=1, hero_name="Vindicta", won=True, played_at=0, duration_s=1200)
    assert "_(pattern, not a one-off)_" in md
