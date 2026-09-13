"""Steam ID conversions.

Deadlock identifies players by their 32-bit Steam *account id* (the low bits of a
SteamID64), also called SteamID3. The community API's ``account_id`` path
parameter always wants this form.
"""

from __future__ import annotations

import re

# SteamID64 for account id 0. SteamID64 = STEAM_BASE + account_id (for individual accounts).
STEAM_BASE = 76561197960265728

_STEAMID2_RE = re.compile(r"^STEAM_[0-5]:([01]):(\d+)$", re.IGNORECASE)
_STEAMID3_RE = re.compile(r"^\[?U:1:(\d+)\]?$", re.IGNORECASE)


class InvalidSteamID(ValueError):
    """Raised when a string cannot be interpreted as any known Steam ID form."""


def to_account_id(value: str | int) -> int:
    """Coerce any common Steam ID representation to a 32-bit account id.

    Accepts:
      * a bare account id (``"123456"`` / ``123456``)
      * a SteamID64 (``"76561198000000000"``)
      * SteamID3 (``"[U:1:123456]"`` or ``"U:1:123456"``)
      * SteamID2 (``"STEAM_1:0:61728"``)
      * a full ``steamcommunity.com/profiles/<id64>`` URL
    """
    if isinstance(value, int):
        return _from_number(value)

    s = value.strip()

    m = _STEAMID3_RE.match(s)
    if m:
        return int(m.group(1))

    m = _STEAMID2_RE.match(s)
    if m:
        y, z = int(m.group(1)), int(m.group(2))
        return z * 2 + y

    m = re.search(r"/profiles/(\d{17})", s)
    if m:
        return _from_number(int(m.group(1)))

    if s.isdigit():
        return _from_number(int(s))

    raise InvalidSteamID(
        f"{value!r} is not a recognised Steam ID. Provide an account id, SteamID64, "
        "SteamID3, SteamID2, or a steamcommunity.com/profiles/ URL."
    )


def _from_number(n: int) -> int:
    if n <= 0:
        raise InvalidSteamID(f"{n} is not a positive Steam id")
    if n >= STEAM_BASE:
        return n - STEAM_BASE
    if n < 2**32:
        return n
    raise InvalidSteamID(f"{n} is out of range for both an account id and a SteamID64")


def to_steamid64(account_id: int) -> int:
    """Inverse of :func:`to_account_id` for individual accounts."""
    return STEAM_BASE + account_id
