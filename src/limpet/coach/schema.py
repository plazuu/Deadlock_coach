"""The coaching report's shape — both the JSON schema sent to the API
(`REPORT_JSON_SCHEMA`, hand-written and fully inlined so nothing depends on
`$ref`/`$defs` support) and the pydantic models the response is validated
against. Keep the two in sync; there's no schema-generation step between them
on purpose — see coach/analyze.py for why (structured-output support for
`$ref` isn't something we've verified, so we don't rely on it).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Mistake(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp_s: int
    theme: str
    what_happened: str
    why_it_mattered: str
    fix: str
    mental: bool = False


class PillarAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment: str
    rating: int = Field(ge=0, le=100)
    strengths: list[str]
    mistakes: list[Mistake]


class FocusItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: Literal["micro", "macro"]
    theme: str
    why: str
    drill: str


class CoachingReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    micro: PillarAssessment
    macro: PillarAssessment
    focus_this_week: list[FocusItem] = Field(min_length=1, max_length=3)
    progress_note: str


def _mistake_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "timestamp_s": {"type": "integer", "description": "game_time_s this happened at"},
            "theme": {"type": "string"},
            "what_happened": {"type": "string"},
            "why_it_mattered": {"type": "string"},
            "fix": {"type": "string"},
            "mental": {
                "type": "boolean",
                "description": "true if this is a tilt/behavioral pattern, not a one-off",
            },
        },
        "required": ["timestamp_s", "theme", "what_happened", "why_it_mattered", "fix", "mental"],
        "additionalProperties": False,
    }


def _pillar_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "assessment": {"type": "string"},
            "rating": {"type": "integer", "minimum": 0, "maximum": 100},
            "strengths": {"type": "array", "items": {"type": "string"}},
            "mistakes": {"type": "array", "items": _mistake_schema()},
        },
        "required": ["assessment", "rating", "strengths", "mistakes"],
        "additionalProperties": False,
    }


REPORT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "micro": _pillar_schema(),
        "macro": _pillar_schema(),
        "focus_this_week": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "dimension": {"type": "string", "enum": ["micro", "macro"]},
                    "theme": {"type": "string"},
                    "why": {"type": "string"},
                    "drill": {"type": "string"},
                },
                "required": ["dimension", "theme", "why", "drill"],
                "additionalProperties": False,
            },
        },
        "progress_note": {"type": "string"},
    },
    "required": ["summary", "micro", "macro", "focus_this_week", "progress_note"],
    "additionalProperties": False,
}
