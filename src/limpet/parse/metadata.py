"""Typed, defensive views over raw match metadata (`match_info`).

Deliberately thin: this does not re-model Valve's protobuf-derived JSON (that
lives in `limpet.api.client` as raw dicts, on purpose — see CLAUDE.md). It just
resolves *our* player within a match and offers a few convenience accessors that
`features/` builds on. Every accessor is defensive against missing/None keys —
Deadlock is in active development and fields come and go between patches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class PlayerNotInMatch(ValueError):
    """Raised when the configured account did not play in the requested match."""


@dataclass(frozen=True)
class MatchView:
    """Wraps one match's `match_info` dict."""

    raw: dict[str, Any]

    @property
    def match_id(self) -> int:
        return self.raw.get("match_id", 0)

    @property
    def duration_s(self) -> int:
        return self.raw.get("duration_s", 0) or 0

    @property
    def winning_team(self) -> int | None:
        return self.raw.get("winning_team")

    @property
    def players(self) -> list[dict[str, Any]]:
        return self.raw.get("players") or []

    @property
    def objectives(self) -> list[dict[str, Any]]:
        """Match-level objective (tower/walker/shrine/…) destruction events.

        Each entry's `team` field is the *owning* team — i.e. the team that
        lost the objective, not the team that destroyed it. Inferred from
        observed data (not documented by the API); re-verify if downstream
        numbers look inverted.
        """
        return self.raw.get("objectives") or []

    @property
    def mid_boss(self) -> list[dict[str, Any]]:
        return self.raw.get("mid_boss") or []

    def player(self, account_id: int) -> dict[str, Any]:
        for p in self.players:
            if p.get("account_id") == account_id:
                return p
        raise PlayerNotInMatch(f"account {account_id} is not a player in this match")

    def team_of(self, account_id: int) -> int:
        return self.player(account_id).get("team", 0)

    def teammates(self, account_id: int) -> list[dict[str, Any]]:
        team = self.team_of(account_id)
        return [
            p for p in self.players if p.get("team") == team and p.get("account_id") != account_id
        ]

    def enemies(self, account_id: int) -> list[dict[str, Any]]:
        team = self.team_of(account_id)
        return [p for p in self.players if p.get("team") != team]

    def average_badge(self, account_id: int) -> int:
        """Our team's average badge for this match — the benchmark bracket key."""
        team = self.team_of(account_id)
        return self.raw.get(f"average_badge_team{team}") or 0


def load(match_metadata: dict[str, Any]) -> MatchView:
    """Build a `MatchView` from a `/v1/matches/{id}/metadata` response."""
    match_info = match_metadata.get("match_info")
    if not match_info:
        raise ValueError("match metadata payload has no 'match_info'")
    return MatchView(raw=match_info)
