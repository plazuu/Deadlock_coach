"""Macro / objective control.

Team-level for Phase 1 — per-player credit for a specific objective push needs
positional/assist data at the destroy event, which isn't in `match_info`
(candidate for the Phase 6 demo query).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..common import Leaf

if TYPE_CHECKING:
    from ...parse.metadata import MatchView

SUB = "objectives"


def extract(view: MatchView, our_team: int) -> list[Leaf]:
    taken = [o for o in view.objectives if o.get("team") != our_team]
    lost = [o for o in view.objectives if o.get("team") == our_team]
    first_taken = min((o["destroyed_time_s"] for o in taken), default=None)

    boss = view.mid_boss
    boss_won = sum(1 for b in boss if b.get("team_claimed") == our_team)
    boss_lost = sum(
        1 for b in boss if b.get("team_claimed") is not None and b["team_claimed"] != our_team
    )

    return [
        Leaf("objectives_taken", "macro", SUB, len(taken), "count"),
        Leaf("objectives_lost", "macro", SUB, len(lost), "count"),
        Leaf("first_objective_taken_time_s", "macro", SUB, first_taken, "s"),
        Leaf("boss_objectives_won", "macro", SUB, boss_won, "count"),
        Leaf("boss_objectives_lost", "macro", SUB, boss_lost, "count"),
    ]
