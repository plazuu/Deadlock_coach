"""Render a `CoachingReport` as Markdown: Summary -> Micro -> Macro -> Focus."""

from __future__ import annotations

import datetime as _dt

from ..coach.schema import CoachingReport, PillarAssessment


def _fmt_timestamp(seconds: int) -> str:
    m, s = divmod(max(0, seconds), 60)
    return f"{m}:{s:02d}"


def _render_pillar(label: str, pillar: PillarAssessment) -> list[str]:
    lines = [f"## {label} — {pillar.rating}/100", "", pillar.assessment, ""]
    if pillar.strengths:
        lines.append("**What went well**")
        lines += [f"- {s}" for s in pillar.strengths]
        lines.append("")
    if pillar.mistakes:
        lines.append("**Key mistakes**")
        for m in pillar.mistakes:
            tag = " _(pattern, not a one-off)_" if m.mental else ""
            lines.append(
                f"- **[{_fmt_timestamp(m.timestamp_s)}] {m.theme}**{tag} — {m.what_happened} "
                f"Why it mattered: {m.why_it_mattered} **Fix:** {m.fix}"
            )
        lines.append("")
    return lines


def render(
    report: CoachingReport,
    *,
    match_id: int,
    hero_name: str,
    won: bool,
    played_at: int,
    duration_s: int,
) -> str:
    when = _dt.datetime.fromtimestamp(played_at).strftime("%Y-%m-%d %H:%M")
    result = "Win" if won else "Loss"
    lines = [
        f"# {hero_name} — {result} — {when} (match {match_id}, {duration_s // 60}m)",
        "",
        report.summary,
        "",
        *_render_pillar("Micro", report.micro),
        *_render_pillar("Macro", report.macro),
        "## Focus this week",
        "",
    ]
    for f in report.focus_this_week:
        lines.append(f"- **[{f.dimension}] {f.theme}** — {f.why} **Drill:** {f.drill}")
    lines += ["", "## Progress", "", report.progress_note, ""]
    return "\n".join(lines)
