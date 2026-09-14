from __future__ import annotations

import json
from pathlib import Path

import pytest

from limpet.parse.metadata import PlayerNotInMatch, load

FIXTURE = Path(__file__).parent / "fixtures" / "match_104887482.json"
ACCOUNT_ID = 173907991  # the player this fixture was captured for


@pytest.fixture
def view():
    return load(json.loads(FIXTURE.read_text()))


def test_top_level_fields(view):
    assert view.match_id == 104887482
    assert view.duration_s == 1868
    assert view.winning_team == 1
    assert len(view.players) == 12


def test_player_lookup(view):
    p = view.player(ACCOUNT_ID)
    assert p["hero_id"] == 17
    assert p["team"] == 0


def test_unknown_player_raises(view):
    with pytest.raises(PlayerNotInMatch):
        view.player(1)


def test_team_helpers(view):
    assert view.team_of(ACCOUNT_ID) == 0
    teammates = view.teammates(ACCOUNT_ID)
    enemies = view.enemies(ACCOUNT_ID)
    assert len(teammates) == 5
    assert len(enemies) == 6
    assert all(p["team"] == 0 for p in teammates)
    assert all(p["team"] == 1 for p in enemies)
    assert ACCOUNT_ID not in {p["account_id"] for p in teammates}


def test_objectives_and_mid_boss_present(view):
    assert len(view.objectives) > 0
    assert len(view.mid_boss) > 0


def test_position_at_resolves_within_player_bounds(view):
    slot = view.player(ACCOUNT_ID)["player_slot"]
    path = view.match_paths[slot]
    pos = view.position_at(slot, 100)
    assert path["x_min"] <= pos["x"] <= path["x_max"]
    assert path["y_min"] <= pos["y"] <= path["y_max"]


def test_position_at_unknown_slot_is_none(view):
    assert view.position_at(9999, 100) is None
