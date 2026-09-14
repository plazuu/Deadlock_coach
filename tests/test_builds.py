from __future__ import annotations

import json
from pathlib import Path

import pytest

from limpet.coach.builds import build_item_context
from limpet.parse.metadata import load

FIXTURES = Path(__file__).parent / "fixtures"
ACCOUNT_ID = 173907991


class FakeAssets:
    """Offline stand-in for `limpet.assets.Assets`, seeded from the real
    lookup of items this fixture match's player purchased."""

    def __init__(self, table: dict[str, dict]):
        self._table = table

    def item(self, item_id: int | None):
        return self._table.get(str(item_id))

    def item_name(self, item_id: int) -> str:
        row = self.item(item_id)
        return row["name"] if row else f"item:{item_id}"

    def hero_name(self, hero_id: int) -> str:
        return f"hero:{hero_id}"


@pytest.fixture
def view():
    return load(json.loads((FIXTURES / "match_104887482.json").read_text()))


@pytest.fixture
def assets():
    return FakeAssets(json.loads((FIXTURES / "items_104887482.json").read_text()))


def test_our_build_is_chronological_real_item_names(view, assets):
    ctx = build_item_context(view, ACCOUNT_ID, assets)
    our_build = ctx["our_build"]
    assert len(our_build) > 0
    assert all(isinstance(r["item"], str) and not r["item"].startswith("item:") for r in our_build)
    times = [r["t"] for r in our_build]
    assert times == sorted(times)


def test_enemies_have_hero_and_at_least_one_nonempty_build(view, assets):
    ctx = build_item_context(view, ACCOUNT_ID, assets)
    enemies = ctx["enemies"]
    assert len(enemies) == 6
    assert all(e["hero"].startswith("hero:") for e in enemies)
    assert any(len(e["build"]) > 0 for e in enemies)
