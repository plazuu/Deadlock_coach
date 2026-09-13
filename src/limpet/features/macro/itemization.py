"""Macro / itemization — timing and adaptation, not the exact build.

`player['items']` mixes ability-point spends in with real shop purchases (see
`docs/api-notes.md`); this leaf filters to asset `type == "upgrade"`, the
actual gold-cost items. Degrades gracefully without `assets`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..common import Leaf

if TYPE_CHECKING:
    from ...assets import Assets

SUB = "itemization"


def extract(player: dict[str, Any], assets: Assets | None) -> list[Leaf]:
    if assets is None:
        return [
            Leaf(
                "items_purchased_total",
                "macro",
                SUB,
                None,
                "count",
                note="requires the asset table to classify purchases",
            )
        ]

    purchases = []
    for it in player.get("items") or []:
        info = assets.item(it.get("item_id"))
        if info and info.get("type") == "upgrade":
            purchases.append({**it, "_tier": info.get("item_tier"), "_cost": info.get("cost", 0)})

    total_cost = sum(p.get("_cost") or 0 for p in purchases)
    sell_count = sum(1 for p in purchases if (p.get("sold_time_s") or 0) > 0)
    tier2_times = [p["game_time_s"] for p in purchases if (p.get("_tier") or 0) >= 2]
    first_tier2 = min(tier2_times, default=None)

    return [
        Leaf("items_purchased_total", "macro", SUB, len(purchases), "count"),
        Leaf("gold_spent_on_items", "macro", SUB, total_cost, "count"),
        Leaf("items_sold_total", "macro", SUB, sell_count, "count"),
        Leaf("first_tier2_item_time_s", "macro", SUB, first_tier2, "s"),
    ]
