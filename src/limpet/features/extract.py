"""Orchestrates every `micro/*` and `macro/*` leaf into one `MatchFeatures`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..assets import Assets
from ..parse.metadata import MatchView
from .common import Leaf
from .macro import awareness, economy, farm_stealing, itemization, objectives, rotations, waves
from .micro import abilities, aim, fights, lasthits, survival, trades


@dataclass
class MatchFeatures:
    micro: dict[str, dict[str, Any]] = field(default_factory=dict)
    macro: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"micro": self.micro, "macro": self.macro}


def _bucket(features: MatchFeatures, leaves: list[Leaf]) -> None:
    for leaf in leaves:
        target = features.micro if leaf.dimension == "micro" else features.macro
        target.setdefault(leaf.sub_dimension, {})[leaf.key] = leaf.to_dict()


def extract(view: MatchView, account_id: int, assets: Assets | None = None) -> MatchFeatures:
    """Compute every feature leaf for `account_id`'s performance in this match."""
    player = view.player(account_id)
    duration_s = view.duration_s
    our_team = view.team_of(account_id)
    team_kills_total = (player.get("kills", 0) or 0) + sum(
        p.get("kills", 0) or 0 for p in view.teammates(account_id)
    )

    features = MatchFeatures()
    for leaves in (
        lasthits.extract(player),
        aim.extract(player),
        abilities.extract(player, assets),
        trades.extract(player),
        fights.extract(player),
        survival.extract(player, duration_s),
        economy.extract(player, duration_s),
        objectives.extract(view, our_team),
        rotations.extract(view, account_id, player, team_kills_total),
        awareness.extract(player),
        itemization.extract(player, assets),
        farm_stealing.extract(view, account_id, player),
        waves.extract(),
    ):
        _bucket(features, leaves)
    return features
