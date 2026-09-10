"""Local cache of Deadlock static assets (heroes, items, ranks).

IDs in match data are meaningless without these lookup tables. They change only
on patches, so we cache them on disk and refresh on a TTL.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import paths
from .api.client import DeadlockClient

DEFAULT_TTL_S = 7 * 24 * 3600  # one week


@dataclass
class _Table:
    fetched_at: float
    rows: list[dict[str, Any]]


class Assets:
    """Name lookups for heroes, items and ranks, backed by an on-disk cache."""

    def __init__(self, client: DeadlockClient, *, ttl_s: int = DEFAULT_TTL_S):
        self._client = client
        self._ttl = ttl_s
        self._cache: dict[str, _Table] = {}
        self._by_id: dict[str, dict[int, dict[str, Any]]] = {}

    # -- public API -------------------------------------------------------

    def refresh(self, *, force: bool = True) -> None:
        for name in ("heroes", "items", "ranks"):
            self._load(name, force=force)

    def hero_name(self, hero_id: int) -> str:
        row = self._lookup("heroes", hero_id)
        return _display_name(row) if row else f"hero:{hero_id}"

    def item_name(self, item_id: int) -> str:
        row = self._lookup("items", item_id)
        return _display_name(row) if row else f"item:{item_id}"

    def hero(self, hero_id: int) -> dict[str, Any] | None:
        return self._lookup("heroes", hero_id)

    def item(self, item_id: int) -> dict[str, Any] | None:
        return self._lookup("items", item_id)

    def rank_name(self, badge: int) -> str:
        """``badge`` is tier*10 + subrank (e.g. 63 -> 'Archon 3')."""
        if badge <= 0:
            return "Obscurus"
        tier, subrank = divmod(badge, 10)
        row = self._lookup("ranks", tier)
        base = _display_name(row) if row else f"tier {tier}"
        return f"{base} {subrank}" if subrank else base

    # -- internals ------------------------------------------------------

    def _cache_file(self, name: str) -> Path:
        return paths.assets_cache_dir() / f"{name}.json"

    def _load(self, name: str, *, force: bool = False) -> _Table:
        if not force and name in self._cache:
            return self._cache[name]

        path = self._cache_file(name)
        if not force and path.exists():
            payload = json.loads(path.read_text())
            if time.time() - payload.get("fetched_at", 0) < self._ttl:
                table = _Table(payload["fetched_at"], payload["rows"])
                self._remember(name, table)
                return table

        rows = getattr(self._client, f"assets_{name}")()
        table = _Table(time.time(), rows)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fetched_at": table.fetched_at, "rows": rows}))
        self._remember(name, table)
        return table

    def _remember(self, name: str, table: _Table) -> None:
        self._cache[name] = table
        index: dict[int, dict[str, Any]] = {}
        for row in table.rows:
            rid = row.get("id") or row.get("tier") or row.get("hero_id")
            if isinstance(rid, int):
                index[rid] = row
        self._by_id[name] = index

    def _lookup(self, name: str, key: int) -> dict[str, Any] | None:
        if name not in self._by_id:
            self._load(name)
        return self._by_id.get(name, {}).get(key)


def _display_name(row: dict[str, Any] | None) -> str:
    if not row:
        return "?"
    for field in ("name", "display_name", "hero_name", "item_name"):
        val = row.get(field)
        if isinstance(val, str) and val:
            return val
    return str(row.get("class_name", "?"))
