"""Micro / lane-phase trading.

Damage dealt vs. taken are cumulative-from-game-start counters in `stats[]`, so
the sample nearest the end of lane phase gives the lane-phase totals directly
— no baseline subtraction needed.
"""

from __future__ import annotations

from typing import Any

from ..common import LANE_PHASE_END_S, Leaf, safe_ratio, stats_at_or_before

SUB = "trades"


def extract(player: dict[str, Any]) -> list[Leaf]:
    snap = stats_at_or_before(player.get("stats"), LANE_PHASE_END_S)
    if not snap:
        return []
    dealt = snap.get("player_damage", 0) or 0
    taken = snap.get("player_damage_taken", 0) or 0
    return [
        Leaf(
            "lane_phase_damage_ratio",
            "micro",
            SUB,
            safe_ratio(dealt, dealt + taken),
            "ratio",
            note="damage dealt vs. taken through ~10:00",
        ),
        Leaf("lane_phase_damage_dealt", "micro", SUB, dealt, "count"),
        Leaf("lane_phase_damage_taken", "micro", SUB, taken, "count"),
    ]
