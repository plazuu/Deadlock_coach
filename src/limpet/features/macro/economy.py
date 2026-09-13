"""Macro / farm routing & economy."""

from __future__ import annotations

from typing import Any

from ..common import Leaf, final_stats, per_min, safe_ratio

SUB = "economy"


def extract(player: dict[str, Any], duration_s: int) -> list[Leaf]:
    fin = final_stats(player.get("stats"))
    if not fin:
        return []
    net_worth = fin.get("net_worth", player.get("net_worth", 0))
    lane = fin.get("gold_lane_creep", 0) or 0
    neutral = fin.get("gold_neutral_creep", 0) or 0
    boss = fin.get("gold_boss", 0) or 0
    farm_total = lane + neutral + boss
    return [
        Leaf("souls_per_min", "macro", SUB, per_min(net_worth, duration_s), "per_min"),
        Leaf("lane_creep_farm_share", "macro", SUB, safe_ratio(lane, farm_total), "ratio"),
        Leaf("neutral_creep_farm_share", "macro", SUB, safe_ratio(neutral, farm_total), "ratio"),
        Leaf("boss_farm_share", "macro", SUB, safe_ratio(boss, farm_total), "ratio"),
        Leaf("gold_denied_total", "macro", SUB, fin.get("gold_denied", 0), "count"),
    ]
