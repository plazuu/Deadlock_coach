"""Macro / rotations & tempo — being where the game is."""

from __future__ import annotations

from typing import Any

from ..common import Leaf, safe_ratio

SUB = "rotations"


def extract(player: dict[str, Any], team_kills_total: int) -> list[Leaf]:
    involved = (player.get("kills", 0) or 0) + (player.get("assists", 0) or 0)
    return [
        Leaf(
            "fight_participation",
            "macro",
            SUB,
            safe_ratio(involved, team_kills_total),
            "ratio",
            note="share of the team's kills you got a kill or assist on",
        ),
        Leaf(
            "avg_response_distance",
            "macro",
            SUB,
            None,
            "units",
            needs_demo=True,
            note="requires a full ally position timeline; not in match_info",
        ),
    ]
