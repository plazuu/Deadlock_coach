"""Item-build context for the coaching briefing: our purchase order and each
enemy's hero + notable purchases, by name and timestamp.

Not a `Leaf` — a build order doesn't reduce to a single benchmarkable scalar
the way the rest of `features/` does. This sits in the briefing as its own
top-level key instead (see `coach/briefing.py`), letting the coach name real
items in itemization critique ("you needed X before the enemy's Y at 9:00")
rather than speaking only in the aggregate counts `features/macro/itemization.py`
computes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..assets import Assets
    from ..parse.metadata import MatchView

# Enemy builds are capped to tier2+ purchases to keep the briefing compact —
# early tier1 items are rarely coaching-relevant and there are up to 6 enemies.
ENEMY_MIN_TIER = 2


def _build_order(player: dict[str, Any], assets: Assets) -> list[dict[str, Any]]:
    purchases = []
    for it in player.get("items") or []:
        info = assets.item(it.get("item_id"))
        if info and info.get("type") == "upgrade":
            purchases.append(
                {
                    "t": it.get("game_time_s"),
                    "item": assets.item_name(it["item_id"]),
                    "tier": info.get("item_tier") or 0,
                }
            )
    return sorted(purchases, key=lambda r: r["t"] or 0)


def build_item_context(view: MatchView, account_id: int, assets: Assets) -> dict[str, Any]:
    """`{"our_build": [{t, item}], "enemies": [{"hero": str, "build": [{t, item}]}]}`."""
    our_build = [
        {"t": r["t"], "item": r["item"]} for r in _build_order(view.player(account_id), assets)
    ]

    enemies = []
    for enemy in view.enemies(account_id):
        full = _build_order(enemy, assets)
        notable = [r for r in full if r["tier"] >= ENEMY_MIN_TIER] or full[-4:]
        enemies.append(
            {
                "hero": assets.hero_name(enemy.get("hero_id", 0)),
                "build": [{"t": r["t"], "item": r["item"]} for r in notable],
            }
        )

    return {"our_build": our_build, "enemies": enemies}
