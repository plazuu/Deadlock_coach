"""Macro / farm-stealing — capitalizing on a teammate's death by taking over
their lane's farm.

First-pass heuristic, no external map/lane data needed: a teammate's death
opens a fixed-length window where their lane is presumably undefended. We
call it "seized" if, in that window, our own position moved toward that
teammate's own lane-phase "zone" (the centroid of *their* positions during
the first `LANE_PHASE_END_S`, self-referential — no map geometry required)
and our net-worth gain rate beat our match-average rate by `RATE_MULTIPLIER`.
`WINDOW_S` and `RATE_MULTIPLIER` are tunable constants, not derived from any
spec — expect to retune once this has been checked against real matches.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..common import LANE_PHASE_END_S, Leaf, euclidean

if TYPE_CHECKING:
    from ...parse.metadata import MatchView

SUB = "farm_stealing"
WINDOW_S = 45
RATE_MULTIPLIER = 1.3


def _lane_zone(view: MatchView, player_slot: int | None) -> dict[str, float] | None:
    if player_slot is None:
        return None
    path = view.match_paths.get(player_slot)
    if not path or not path.get("x_pos"):
        return None
    interval_s = path.get("interval_s", 1.0)
    sample_count = min(len(path["x_pos"]), max(1, int(LANE_PHASE_END_S / interval_s)))
    positions = [
        p
        for i in range(sample_count)
        if (p := view.position_at(player_slot, i * interval_s)) is not None
    ]
    if not positions:
        return None
    return {
        "x": sum(p["x"] for p in positions) / len(positions),
        "y": sum(p["y"] for p in positions) / len(positions),
    }


def _net_worth_rate(stats: list[dict[str, Any]], t0: float, t1: float) -> float | None:
    if t1 <= t0 or not stats:
        return None
    before = [s for s in stats if s.get("time_stamp_s", 0) <= t0]
    after = [s for s in stats if s.get("time_stamp_s", 0) <= t1]
    if not after:
        return None
    start_nw = before[-1].get("net_worth", 0) if before else 0
    end_nw = after[-1].get("net_worth", 0)
    return (end_nw - start_nw) / (t1 - t0)


def extract(view: MatchView, account_id: int, player: dict[str, Any]) -> list[Leaf]:
    our_slot = player.get("player_slot")
    stats = player.get("stats") or []
    match_avg_rate = _net_worth_rate(stats, 0, view.duration_s)

    if our_slot is None or not view.match_paths or match_avg_rate is None:
        return [
            Leaf(
                "farm_steal_windows_taken",
                "macro",
                SUB,
                None,
                "count",
                needs_demo=True,
                note="requires this match's position data (match_paths) and net-worth samples",
            )
        ]

    our_zone = _lane_zone(view, our_slot)
    opportunities = 0
    taken = 0
    for mate in view.teammates(account_id):
        mate_zone = _lane_zone(view, mate.get("player_slot"))
        for d in mate.get("death_details") or []:
            t = d.get("game_time_s", 0)
            opportunities += 1
            our_pos = view.position_at(our_slot, t + WINDOW_S / 2)
            window_rate = _net_worth_rate(stats, t, t + WINDOW_S)
            if not (mate_zone and our_pos and window_rate is not None):
                continue
            dist_to_mate = euclidean(our_pos, mate_zone)
            dist_to_own = euclidean(our_pos, our_zone) if our_zone else None
            closer_to_mate = dist_to_own is None or dist_to_mate < dist_to_own
            if closer_to_mate and window_rate > match_avg_rate * RATE_MULTIPLIER:
                taken += 1

    return [
        Leaf(
            "farm_steal_opportunities",
            "macro",
            SUB,
            opportunities,
            "count",
            note="teammate deaths this match — each nominally frees up their lane",
        ),
        Leaf(
            "farm_steal_windows_taken",
            "macro",
            SUB,
            taken,
            "count",
            note="first-pass heuristic: near a dead teammate's lane zone + an "
            "above-average net-worth window right after their death",
        ),
    ]
