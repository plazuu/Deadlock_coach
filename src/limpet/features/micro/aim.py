"""Micro / weapon accuracy."""

from __future__ import annotations

from typing import Any

from ..common import Leaf, final_stats, safe_ratio

SUB = "aim"


def extract(player: dict[str, Any]) -> list[Leaf]:
    fin = final_stats(player.get("stats"))
    if not fin:
        return []
    hit = fin.get("shots_hit", 0) or 0
    miss = fin.get("shots_missed", 0) or 0
    bullets_hit = fin.get("hero_bullets_hit", 0) or 0
    crit = fin.get("hero_bullets_hit_crit", 0) or 0
    kills = player.get("kills", 0) or 0
    return [
        Leaf("accuracy", "micro", SUB, safe_ratio(hit, hit + miss), "ratio"),
        Leaf("crit_rate", "micro", SUB, safe_ratio(crit, bullets_hit), "ratio"),
        Leaf(
            "headshot_kill_share",
            "micro",
            SUB,
            safe_ratio(fin.get("headshot_kills", 0), kills),
            "ratio",
            note="share of kills that were headshots, not a per-shot rate",
        ),
    ]
