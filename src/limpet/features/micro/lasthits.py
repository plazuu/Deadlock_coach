"""Micro / last-hitting & denies.

`stats[].possible_creeps` is a cumulative count of last hits that were
available to the player; `creep_kills` is how many they actually took.
"""

from __future__ import annotations

from typing import Any

from ..common import Leaf, final_stats, safe_ratio

SUB = "last_hitting"


def extract(player: dict[str, Any]) -> list[Leaf]:
    fin = final_stats(player.get("stats"))
    if not fin:
        return []
    creep_kills = fin.get("creep_kills", 0) or 0
    possible = fin.get("possible_creeps", 0) or 0
    return [
        Leaf("cs_efficiency", "micro", SUB, safe_ratio(creep_kills, possible), "ratio"),
        Leaf("last_hits_total", "micro", SUB, creep_kills, "count"),
        Leaf("neutral_kills_total", "micro", SUB, fin.get("neutral_kills", 0), "count"),
        Leaf(
            "denies_total",
            "micro",
            SUB,
            fin.get("denies", player.get("denies", 0)),
            "count",
        ),
    ]
