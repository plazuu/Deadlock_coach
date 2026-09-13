"""Macro / map awareness — getting caught, not losing the fight once caught."""

from __future__ import annotations

from typing import Any

from ..common import Leaf, euclidean

SUB = "awareness"


def extract(player: dict[str, Any]) -> list[Leaf]:
    deaths = player.get("death_details") or []
    early_deaths = sum(1 for d in deaths if d.get("game_time_s", 0) < 600)
    distances = [
        dist
        for d in deaths
        if (dist := euclidean(d.get("death_pos"), d.get("killer_pos"))) is not None
    ]
    avg_distance = sum(distances) / len(distances) if distances else None

    return [
        Leaf("deaths_total", "macro", SUB, len(deaths), "count"),
        Leaf(
            "deaths_before_10min",
            "macro",
            SUB,
            early_deaths,
            "count",
            note="early deaths usually mean a lane gank you didn't see coming",
        ),
        Leaf(
            "avg_engagement_distance",
            "macro",
            SUB,
            avg_distance,
            "units",
            note="distance from killer at time of death; large values suggest "
            "being picked off from range/mobility you didn't track",
        ),
    ]
