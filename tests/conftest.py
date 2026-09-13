from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point Limpet's filesystem layout at a throwaway directory."""
    monkeypatch.setenv("LIMPET_DATA_DIR", str(tmp_path))
    from limpet import paths

    importlib.reload(paths)
    paths.ensure_tree()
    yield tmp_path


@pytest.fixture
def fake_history_entry() -> dict:
    return {
        "account_id": 111,
        "match_id": 900001,
        "hero_id": 15,
        "hero_level": 24,
        "start_time": 1_726_000_000,
        "game_mode": 1,
        "match_mode": 4,
        "player_team": 0,
        "player_kills": 7,
        "player_deaths": 4,
        "player_assists": 11,
        "denies": 12,
        "last_hits": 180,
        "net_worth": 24_500,
        "match_duration_s": 2_100,
        "match_result": 0,
        "player_match_outcome": 1,
        "objectives_mask_team0": 0,
        "objectives_mask_team1": 0,
    }
