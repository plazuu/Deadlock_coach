from __future__ import annotations

from limpet.assets import Assets


class FakeClient:
    def __init__(self):
        self.calls = 0

    def assets_heroes(self):
        self.calls += 1
        return [{"id": 15, "name": "Bebop"}, {"id": 1, "name": "Abrams"}]

    def assets_items(self):
        return [{"id": 1234, "name": "Monster Rounds"}]

    def assets_ranks(self):
        return [{"tier": 6, "name": "Archon"}, {"tier": 0, "name": "Obscurus"}]


def test_lookups(data_dir):
    fc = FakeClient()
    assets = Assets(fc)
    assert assets.hero_name(15) == "Bebop"
    assert assets.item_name(1234) == "Monster Rounds"
    assert assets.hero_name(999) == "hero:999"
    assert assets.rank_name(63) == "Archon 3"
    assert assets.rank_name(0) == "Obscurus"


def test_disk_cache_avoids_refetch(data_dir):
    fc = FakeClient()
    Assets(fc).hero_name(1)
    assert fc.calls == 1
    # a fresh instance should read the cached file, not call the client again
    Assets(fc).hero_name(1)
    assert fc.calls == 1


def test_force_refresh(data_dir):
    fc = FakeClient()
    a = Assets(fc)
    a.hero_name(1)
    a.refresh(force=True)
    assert fc.calls == 2
