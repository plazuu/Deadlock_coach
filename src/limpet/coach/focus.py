"""Reconcile a match's coaching report against tracked `focus_areas`.

This is the piece that makes Limpet a long-term coach rather than a per-match
report generator — see `docs/PLAN.md` §5a. Matching a new match's themes
against previously-tracked ones by exact string equality would miss "Last-hit
conversion" vs "CS efficiency" naming the same underlying issue two games
apart, so a cheap model does the matching: given the tracked focus areas and
this match's report, it decides — per tracked item — whether this match still
shows it (`active`), shows it less (`improving`), or doesn't show it at all
(`resolved`), and separately flags any `focus_this_week` theme that doesn't
match anything already tracked as a new one to start following.

No schema-generation step from the pydantic models here either, same reason as
`coach/schema.py` — hand-inlined JSON schema, no `$ref`/`$defs`.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Literal

import anthropic
from pydantic import BaseModel, ConfigDict

from ..store import db
from .analyze import CoachError
from .schema import CoachingReport

DEFAULT_MODEL = "claude-haiku-4-5"

FOCUS_SYSTEM_PROMPT = """\
You maintain a Deadlock player's long-term coaching profile. You're given the \
issues currently being tracked from past matches ("tracked_focus_areas") and one \
new match's full coaching report ("this_match").

For EVERY tracked focus area, decide its status after this match:
- "active": this match's mistakes or focus_this_week still clearly show this issue.
- "improving": this match shows it less than before, or the report's progress_note \
says so explicitly — the issue isn't gone, but it's trending the right way.
- "resolved": this match shows no sign of it at all, and nothing contradicts that.

Prefer "active" over "resolved" when unsure — one clean game isn't enough evidence \
that a real pattern is fixed. Never invent evidence not present in this_match.

Then check this match's focus_this_week: for any item that is NOT a restatement of \
an already-tracked focus area (same underlying issue, different wording), list it \
as a new focus area to start tracking. Items that just restate a tracked one should \
NOT be listed as new — they're covered by that tracked item's status update instead.

Return one entry in `updates` for every tracked focus area you were given — never \
skip one.\
"""


class FocusAreaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    focus_area_id: int
    status: Literal["active", "improving", "resolved"]


class NewFocusItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: Literal["micro", "macro"]
    theme: str


class FocusReconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updates: list[FocusAreaUpdate]
    new_focus_areas: list[NewFocusItem]


FOCUS_RECONCILIATION_SCHEMA = {
    "type": "object",
    "properties": {
        "updates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "focus_area_id": {"type": "integer"},
                    "status": {"type": "string", "enum": ["active", "improving", "resolved"]},
                },
                "required": ["focus_area_id", "status"],
                "additionalProperties": False,
            },
        },
        "new_focus_areas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dimension": {"type": "string", "enum": ["micro", "macro"]},
                    "theme": {"type": "string"},
                },
                "required": ["dimension", "theme"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["updates", "new_focus_areas"],
    "additionalProperties": False,
}


def _build_context(existing: list[sqlite3.Row], report: CoachingReport) -> dict:
    return {
        "tracked_focus_areas": [
            {"id": r["id"], "dimension": r["dimension"], "theme": r["theme"], "status": r["status"]}
            for r in existing
        ],
        "this_match": {
            "progress_note": report.progress_note,
            "focus_this_week": [
                {"dimension": f.dimension, "theme": f.theme} for f in report.focus_this_week
            ],
            "mistakes": [
                {"dimension": dimension, "theme": m.theme, "what_happened": m.what_happened}
                for dimension, pillar in (("micro", report.micro), ("macro", report.macro))
                for m in pillar.mistakes
            ],
            "strengths": {"micro": report.micro.strengths, "macro": report.macro.strengths},
        },
    }


def _classify(
    client: anthropic.Anthropic,
    existing: list[sqlite3.Row],
    report: CoachingReport,
    *,
    model: str,
) -> FocusReconciliation:
    try:
        response = client.messages.create(
            model=model,
            max_tokens=2000,
            system=FOCUS_SYSTEM_PROMPT,
            output_config={
                "format": {"type": "json_schema", "schema": FOCUS_RECONCILIATION_SCHEMA}
            },
            messages=[{"role": "user", "content": json.dumps(_build_context(existing, report))}],
        )
    except anthropic.AuthenticationError as e:
        raise CoachError("Anthropic authentication failed during focus-area tracking.") from e
    except anthropic.RateLimitError as e:
        raise CoachError(f"Anthropic rate limit hit during focus-area tracking: {e}") from e
    except anthropic.APIStatusError as e:
        raise CoachError(f"Anthropic API error ({e.status_code}) during focus tracking.") from e
    except anthropic.APIConnectionError as e:
        raise CoachError(f"Could not reach the Anthropic API for focus tracking: {e}") from e

    if response.stop_reason == "refusal":
        raise CoachError("Claude declined the focus-area reconciliation step.")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise CoachError("No text content in the focus-reconciliation response.")

    try:
        return FocusReconciliation.model_validate(json.loads(text))
    except Exception as e:
        raise CoachError(f"Focus-reconciliation response didn't match its schema: {e}") from e


def reconcile(
    client: anthropic.Anthropic,
    conn: sqlite3.Connection,
    match_id: int,
    report: CoachingReport,
    now: int,
    *,
    model: str = DEFAULT_MODEL,
) -> None:
    """Update `focus_areas` from one match's coaching report.

    With nothing tracked yet, every `focus_this_week` item just opens a new
    row — no need to spend a call asking a model to match against nothing.
    """
    existing = db.active_focus_areas(conn)

    if not existing:
        for item in report.focus_this_week:
            db.open_focus_area(conn, match_id, item.dimension, item.theme, now)
        return

    result = _classify(client, existing, report, model=model)

    updates_by_id = {u.focus_area_id: u.status for u in result.updates}
    for row in existing:
        status = updates_by_id.get(row["id"])
        if status is not None:
            db.update_focus_area_status(conn, row["id"], status, match_id, now)

    for item in result.new_focus_areas:
        db.open_focus_area(conn, match_id, item.dimension, item.theme, now)
