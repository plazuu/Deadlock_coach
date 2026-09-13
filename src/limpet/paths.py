"""Filesystem layout for Limpet's local state.

Everything Limpet persists lives under a single data directory:

    <data_dir>/
      config.json            written by `limpet init`
      limpet.db              SQLite: matches, features, reports, focus areas
      cache/
        assets/              heroes.json, items.json, ranks.json (+ fetched_at)
        matches/<id>/        metadata.json, salts.json, demo query results
      reports/               <played_at>_<match_id>.md
      digests/               YYYY-MM-DD.md  (one line per analysed match)
      PROGRESS.md            running longitudinal summary

The data directory defaults to a platform data dir but is overridable with the
``LIMPET_DATA_DIR`` environment variable (used heavily in tests).
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "limpet"


def data_dir() -> Path:
    override = os.environ.get("LIMPET_DATA_DIR")
    root = Path(override).expanduser() if override else Path(user_data_dir(APP_NAME))
    return root


def config_path() -> Path:
    return data_dir() / "config.json"


def db_path() -> Path:
    return data_dir() / "limpet.db"


def assets_cache_dir() -> Path:
    return data_dir() / "cache" / "assets"


def match_cache_dir(match_id: int) -> Path:
    return data_dir() / "cache" / "matches" / str(match_id)


def reports_dir() -> Path:
    return data_dir() / "reports"


def digests_dir() -> Path:
    return data_dir() / "digests"


def progress_path() -> Path:
    return data_dir() / "PROGRESS.md"


def ensure_tree() -> None:
    """Create the directory skeleton. Safe to call repeatedly."""
    for d in (
        data_dir(),
        assets_cache_dir(),
        data_dir() / "cache" / "matches",
        reports_dir(),
        digests_dir(),
    ):
        d.mkdir(parents=True, exist_ok=True)
