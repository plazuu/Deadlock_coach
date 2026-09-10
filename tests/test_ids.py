from __future__ import annotations

import pytest

from limpet.ids import STEAM_BASE, InvalidSteamID, to_account_id, to_steamid64


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (123456, 123456),
        ("123456", 123456),
        (STEAM_BASE + 123456, 123456),
        (str(STEAM_BASE + 123456), 123456),
        ("[U:1:123456]", 123456),
        ("U:1:123456", 123456),
        ("STEAM_1:0:61728", 123456),
        ("STEAM_0:1:61728", 123457),
        ("https://steamcommunity.com/profiles/76561198000000000", 76561198000000000 - STEAM_BASE),
    ],
)
def test_to_account_id(value, expected):
    assert to_account_id(value) == expected


def test_roundtrip():
    assert to_account_id(to_steamid64(4242)) == 4242


@pytest.mark.parametrize("bad", ["", "not-an-id", "0", "-5", "STEAM_9:0:1"])
def test_invalid(bad):
    with pytest.raises(InvalidSteamID):
        to_account_id(bad)
