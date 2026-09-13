"""Micro / ability usage.

`player['items']` is a unified purchase log: entries whose asset `type` is
`"ability"` are ability-point spends (leveling an ability), not shop
purchases — see `docs/api-notes.md`. Classifying them needs the asset table,
so this leaf degrades gracefully (returns fewer leaves, each `needs_demo`-free
but noted) when `assets` isn't available.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..common import Leaf, final_stats, safe_ratio

if TYPE_CHECKING:
    from ...assets import Assets

SUB = "abilities"


def extract(player: dict[str, Any], assets: Assets | None) -> list[Leaf]:
    fin = final_stats(player.get("stats"))
    leaves: list[Leaf] = []

    if fin:
        kills = player.get("kills", 0) or 0
        leaves.append(
            Leaf(
                "ability_kill_share",
                "micro",
                SUB,
                safe_ratio(fin.get("ability_kills", 0), kills),
                "ratio",
            )
        )
        leaves.append(Leaf("ability_points_used", "micro", SUB, fin.get("ability_points"), "count"))

    if assets is None:
        return leaves

    ability_buys = [
        it
        for it in player.get("items") or []
        if (a := assets.item(it.get("item_id"))) and a.get("type") == "ability"
    ]
    first_point_time = min((it["game_time_s"] for it in ability_buys), default=None)
    points_by_10min = sum(1 for it in ability_buys if it.get("game_time_s", 1e9) <= 600)
    leaves.append(Leaf("first_ability_point_time_s", "micro", SUB, first_point_time, "s"))
    leaves.append(Leaf("ability_points_by_10min", "micro", SUB, points_by_10min, "count"))
    return leaves
