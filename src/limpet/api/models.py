"""Typed models for the well-specified deadlock-api.com responses.

Match *metadata* is deliberately left as a raw dict — it is a direct projection
of Valve's ``CMsgMatchMetaDataContents`` protobuf, changes with patches, and is
normalised defensively downstream in ``limpet.parse``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

# player_match_outcome values (see OpenAPI description)
OUTCOME_WIN = 1
OUTCOME_LOSS = 2

# match history "match_mode" ints (Valve enum, community-observed)
MATCH_MODE_NAMES = {1: "unranked", 2: "private_lobby", 3: "coop_bot", 4: "ranked", 5: "server_test"}


class MatchHistoryEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    account_id: int
    match_id: int
    hero_id: int
    hero_level: int
    start_time: int  # unix seconds
    game_mode: int
    match_mode: int
    player_team: int
    player_kills: int
    player_deaths: int
    player_assists: int
    denies: int
    last_hits: int
    net_worth: int
    match_duration_s: int
    match_result: int
    player_match_outcome: int
    objectives_mask_team0: int
    objectives_mask_team1: int
    abandoned_time_s: int | None = None

    @property
    def won(self) -> bool:
        return self.player_match_outcome == OUTCOME_WIN

    @property
    def abandoned(self) -> bool:
        return bool(self.abandoned_time_s)

    @property
    def kda(self) -> str:
        return f"{self.player_kills}/{self.player_deaths}/{self.player_assists}"

    @property
    def match_mode_name(self) -> str:
        return MATCH_MODE_NAMES.get(self.match_mode, str(self.match_mode))


class PlayerRank(BaseModel):
    model_config = ConfigDict(extra="allow")

    account_id: int | None = None
    badge: int = 0
    rank: int = 0
    subrank: int = 0

    @property
    def is_ranked(self) -> bool:
        return self.badge > 0

    @property
    def average_badge(self) -> int:
        """The 0-116 ``*_average_badge`` scale the analytics endpoints filter on.

        The API's ``badge`` field is already ``tier*10 + subrank``; fall back to
        composing it if only the split fields are present.
        """
        return self.badge or (self.rank * 10 + self.subrank)
