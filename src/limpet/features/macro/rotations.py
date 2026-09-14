"""Macro / rotations & tempo — being where the game is."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..common import Leaf, euclidean, safe_ratio

if TYPE_CHECKING:
    from ...parse.metadata import MatchView

SUB = "rotations"


def extract(
    view: MatchView, account_id: int, player: dict[str, Any], team_kills_total: int
) -> list[Leaf]:
    involved = (player.get("kills", 0) or 0) + (player.get("assists", 0) or 0)

    our_slot = player.get("player_slot")
    distances = []
    if our_slot is not None:
        for mate in view.teammates(account_id):
            for d in mate.get("death_details") or []:
                pos = view.position_at(our_slot, d.get("game_time_s", 0))
                dp = d.get("death_pos")
                if pos and dp and (dist := euclidean(pos, dp)) is not None:
                    distances.append(dist)
    avg_distance = sum(distances) / len(distances) if distances else None

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
            avg_distance,
            "units",
            needs_demo=avg_distance is None,
            note="distance from teammate deaths at the moment they happened; "
            "lower means you were closer to help or already fighting",
        ),
    ]
