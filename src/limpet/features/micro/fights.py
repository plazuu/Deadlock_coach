"""Micro / fight execution.

Deliberately excludes map-level participation (that's macro/rotations.py) —
this is about what happened once you were already in the fight.
"""

from __future__ import annotations

from typing import Any

from ..common import Leaf, final_stats, safe_ratio

SUB = "fights"


def extract(player: dict[str, Any]) -> list[Leaf]:
    fin = final_stats(player.get("stats"))
    deaths = player.get("deaths", 0) or 0
    death_details = player.get("death_details") or []
    ttk = [d["time_to_kill_s"] for d in death_details if d.get("time_to_kill_s") is not None]
    avg_ttk = sum(ttk) / len(ttk) if ttk else None

    leaves = [
        Leaf(
            "avg_time_to_kill_when_killed",
            "micro",
            SUB,
            avg_ttk,
            "s",
            note="how long the killing blow took once a fight on you started; "
            "low = burst down fast, high = you survived and traded",
        ),
    ]
    if fin:
        leaves.append(
            Leaf(
                "damage_per_death",
                "micro",
                SUB,
                safe_ratio(fin.get("player_damage", 0), deaths),
                "count",
            )
        )
        leaves.append(
            Leaf(
                "self_healing_per_death",
                "micro",
                SUB,
                safe_ratio(fin.get("self_healing", 0), deaths),
                "count",
            )
        )
    return leaves
