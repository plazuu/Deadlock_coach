"""Micro / survivability — damage taken, mitigated, and self-sustain.

Death *timing and location* (getting caught) is a map-awareness question, not
an execution one — see `features/macro/awareness.py`.
"""

from __future__ import annotations

from typing import Any

from ..common import Leaf, final_stats, per_min, safe_ratio

SUB = "survival"


def extract(player: dict[str, Any], duration_s: int) -> list[Leaf]:
    fin = final_stats(player.get("stats"))
    if not fin:
        return []
    taken = fin.get("player_damage_taken", 0) or 0
    mitigated = fin.get("damage_mitigated", 0) or 0
    return [
        Leaf("damage_taken_per_min", "micro", SUB, per_min(taken, duration_s), "per_min"),
        Leaf(
            "damage_mitigated_ratio",
            "micro",
            SUB,
            safe_ratio(mitigated, taken + mitigated),
            "ratio",
        ),
        Leaf(
            "self_healing_per_min",
            "micro",
            SUB,
            per_min(fin.get("self_healing", 0), duration_s),
            "per_min",
        ),
    ]
