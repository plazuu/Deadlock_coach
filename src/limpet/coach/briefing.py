"""Assemble the compact, token-bounded briefing sent to Claude.

Raw match metadata (can be megabytes) is never sent — only the already-compact
`MatchFeatures` JSON plus a small match-context header and any active focus
areas from prior matches (empty until Phase 4's `coach/focus.py` exists to
populate `focus_areas`; the query is wired now so the slot is real).
"""

from __future__ import annotations

from typing import Any


def estimate_tokens(text: str) -> int:
    """Cheap ~4-chars/token heuristic. Good enough to catch a runaway briefing
    without spending a request on `messages.count_tokens` for every match."""
    return len(text) // 4


def build(
    features_dict: dict[str, Any],
    *,
    match_id: int,
    hero_name: str,
    won: bool,
    duration_s: int,
    rank_name: str,
    active_focus_areas: list[dict[str, Any]] | None = None,
    item_builds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    briefing = {
        "match_id": match_id,
        "hero": hero_name,
        "result": "win" if won else "loss",
        "duration_min": round(duration_s / 60, 1),
        "rank_bracket": rank_name,
        "active_focus_areas": active_focus_areas or [],
        "features": features_dict,
    }
    if item_builds is not None:
        briefing["item_builds"] = item_builds
    return briefing
