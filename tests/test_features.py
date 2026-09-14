from __future__ import annotations

import json
from pathlib import Path

import pytest

from limpet.features.extract import extract
from limpet.parse.metadata import load

FIXTURES = Path(__file__).parent / "fixtures"
ACCOUNT_ID = 173907991


class FakeAssets:
    """A tiny offline stand-in for `limpet.assets.Assets`, seeded from a real
    lookup of the exact items this fixture match's player purchased."""

    def __init__(self, table: dict[str, dict]):
        self._table = table

    def item(self, item_id: int | None):
        return self._table.get(str(item_id))


@pytest.fixture
def view():
    return load(json.loads((FIXTURES / "match_104887482.json").read_text()))


@pytest.fixture
def assets():
    return FakeAssets(json.loads((FIXTURES / "items_104887482.json").read_text()))


def leaf(features, dimension: str, sub_dimension: str, key: str):
    return getattr(features, dimension)[sub_dimension][key]


def test_extract_buckets_into_micro_and_macro(view, assets):
    features = extract(view, ACCOUNT_ID, assets)
    assert set(features.micro) == {
        "last_hitting",
        "aim",
        "abilities",
        "trades",
        "fights",
        "survival",
    }
    assert set(features.macro) == {
        "economy",
        "objectives",
        "rotations",
        "awareness",
        "itemization",
        "farm_stealing",
        "wave_management",
    }


def test_last_hitting(view, assets):
    features = extract(view, ACCOUNT_ID, assets)
    lh = features.micro["last_hitting"]
    assert lh["last_hits_total"]["value"] == 71
    assert lh["neutral_kills_total"]["value"] == 23
    assert lh["cs_efficiency"]["value"] == pytest.approx(71 / 101)


def test_aim(view, assets):
    features = extract(view, ACCOUNT_ID, assets)
    aim = features.micro["aim"]
    assert aim["accuracy"]["value"] == pytest.approx(681 / (681 + 825))


def test_abilities_needs_the_asset_table(view, assets):
    with_assets = extract(view, ACCOUNT_ID, assets)
    without_assets = extract(view, ACCOUNT_ID, None)

    ab = with_assets.micro["abilities"]
    assert ab["first_ability_point_time_s"]["value"] == 16
    assert ab["ability_points_by_10min"]["value"] == 7

    # degraded gracefully: the asset-dependent leaves just aren't present
    assert "first_ability_point_time_s" not in without_assets.micro["abilities"]


def test_itemization_needs_the_asset_table(view, assets):
    features = extract(view, ACCOUNT_ID, assets)
    it = features.macro["itemization"]
    assert it["items_purchased_total"]["value"] == 16
    assert it["gold_spent_on_items"]["value"] == 28000
    assert it["items_sold_total"]["value"] == 5
    assert it["first_tier2_item_time_s"]["value"] == 280

    degraded = extract(view, ACCOUNT_ID, None).macro["itemization"]
    assert degraded["items_purchased_total"]["value"] is None
    assert "note" in degraded["items_purchased_total"]


def test_awareness_death_positions(view, assets):
    features = extract(view, ACCOUNT_ID, assets)
    aw = features.macro["awareness"]
    assert aw["deaths_total"]["value"] == 8  # matches player_deaths in the fixture
    assert aw["deaths_before_10min"]["value"] >= 1
    assert aw["avg_engagement_distance"]["value"] > 0


def test_objectives_are_team_level(view, assets):
    features = extract(view, ACCOUNT_ID, assets)
    obj = features.macro["objectives"]
    # this player's team (0) lost, so they should have lost more objectives
    # than they took
    assert obj["objectives_lost"]["value"] >= obj["objectives_taken"]["value"]


def test_wave_management_is_honestly_stubbed(view, assets):
    features = extract(view, ACCOUNT_ID, assets)
    wm = features.macro["wave_management"]["wave_management"]
    assert wm["value"] is None
    assert wm["needs_demo"] is True


def test_farm_stealing_counts_are_sane(view, assets):
    features = extract(view, ACCOUNT_ID, assets)
    fs = features.macro["farm_stealing"]
    assert fs["farm_steal_opportunities"]["value"] >= 0
    assert fs["farm_steal_windows_taken"]["value"] >= 0
    assert fs["farm_steal_windows_taken"]["value"] <= fs["farm_steal_opportunities"]["value"]


def test_farm_stealing_degrades_without_player_slot(view, assets):
    from limpet.features.macro import farm_stealing

    player = view.player(ACCOUNT_ID)
    leaves = farm_stealing.extract(view, ACCOUNT_ID, {**player, "player_slot": None})
    assert len(leaves) == 1
    assert leaves[0].value is None
    assert leaves[0].needs_demo is True


def test_rotations_response_distance_from_match_paths(view, assets):
    features = extract(view, ACCOUNT_ID, assets)
    rot = features.macro["rotations"]
    assert "needs_demo" not in rot["avg_response_distance"]
    assert rot["avg_response_distance"]["value"] > 0
    assert 0 <= rot["fight_participation"]["value"] <= 1
