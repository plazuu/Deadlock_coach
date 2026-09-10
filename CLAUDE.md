# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Limpet is a local, single-user AI coach for the game Deadlock. It pulls the
user's match data from the [deadlock-api.com](https://api.deadlock-api.com)
community API, extracts performance features, benchmarks them against players at
the same rank, and (not yet built) has Claude write per-game coaching reports
that track recurring weaknesses over time.

- **`docs/PLAN.md`** — full design and phased roadmap. Read this before starting
  any non-trivial feature; it defines the module layout the codebase is growing into.
- **`docs/api-notes.md`** — verified deadlock-api.com endpoint shapes, auth, rate
  limits, and the `match_info` schema. Trust this over guessing; re-verify against
  `https://api.deadlock-api.com/openapi.json` if something looks off.

Current state: Phase 0 + the ingest slice of Phase 1. Implemented = API client,
config, asset cache, SQLite store, and the `init/whoami/sync/matches/fetch` CLI.
Not yet built = `parse/`, `features/`, `coach/`, `report/`, the `watch` poller.

**Organizing principle: micro and macro.** Every feature, coaching observation,
focus area, and drill is tagged `micro` (mechanical execution — CS, aim,
abilities, fights, dodging) or `macro` (map-level decisions — farm routing,
rotations, objectives, itemization timing, getting caught). The report, the
`match_features` JSON (`{micro:{…}, macro:{…}}`), the `focus_areas.dimension`
column, and the progress trend lines are all split this way. See PLAN.md §1 for
the sub-dimension rubric. When building `features/` or `coach/`, keep the two
pillars separate end to end.

## Commands

The host has Python 3.14 and **no uv/poetry** — a plain venv at `.venv`. Use the
Makefile (`make help` lists all targets):

```sh
make install                      # python3 -m venv .venv + pip install -e ".[dev]"
make test                         # pytest
make lint                         # ruff check + ruff format --check
make fmt                          # ruff check --fix + ruff format
make check                        # lint + test  (run before committing)
make run ARGS="matches --limit 5" # invoke the CLI
```

Run one test:

```sh
.venv/bin/python -m pytest tests/test_client.py::test_retries_on_429_then_succeeds
```

Live smoke test against the real API (no config file needed — env vars win):

```sh
LIMPET_DATA_DIR=/tmp/limpet-x LIMPET_STEAM_ID=173907991 .venv/bin/limpet matches
```

Docker (image reads all config from `LIMPET_*` env, so no interactive `init`):

```sh
make docker-build
make docker-run ARGS="sync"
```

## Architecture

Data flows through distinct layers; keep responsibilities where they are:

```
api/client.py   raw HTTP → dict/list. Auth, rate limiting, retry. The ONLY file
                that knows deadlock-api.com URL shapes.
api/models.py   pydantic models for the few well-specified responses
                (MatchHistoryEntry, PlayerRank). Match metadata is deliberately
                NOT modeled — it's a raw projection of Valve's protobuf and
                changes with patches.
assets.py       hero/item/rank id→name lookups, disk-cached with a TTL.
ingest.py       fetch + persist: match-history sync, raw metadata → cache/.
store/db.py     SQLite. An index and workflow tracker, NOT the source of truth.
cli.py          Typer app. Thin — delegates to the modules above.
```

### Source-of-truth rule

Raw API payloads written under `<data_dir>/cache/` are canonical. SQLite
(`matches`, `match_features`, `reports`, `focus_areas`, `progress_snapshots`) is
a derived index — every computed table must be reconstructible from `cache/`
without re-fetching. When adding a pipeline stage, cache the raw input first,
then compute.

### Filesystem layout

`paths.py` is the single authority for where anything lives. Everything is under
one data directory, chosen by `LIMPET_DATA_DIR` if set, else a platform data dir.
Never build paths ad hoc — add a function to `paths.py`. Tests depend on this:
the `data_dir` fixture sets `LIMPET_DATA_DIR` and `importlib.reload(paths)`.

### Config resolution

`config.py` (`Settings`, pydantic-settings): `LIMPET_*` env vars > `config.json`
in the data dir > defaults. `Settings.account_id` normalizes any Steam ID form
(account id / SteamID64 / `[U:1:…]` / `STEAM_1:…` / profile URL) via
`ids.to_account_id`. Code paths that need identity call `settings.account_id`,
never `settings.steam_id` directly.

## Conventions

- `from __future__ import annotations` at the top of every module.
- ruff, line length 100, rule set `E,F,I,UP,B,SIM` (see `pyproject.toml`).
- **New API endpoints go on `DeadlockClient` and nowhere else.** Add only
  endpoints Limpet actually uses. Return parsed JSON; wrap in a model only if the
  response is stable and documented.
- pydantic models that mirror API responses use `extra="allow"` so unknown
  fields (patch additions) don't break parsing.
- `DeadlockClient` retries transport errors + 429/500/502/503/504 (4 attempts,
  exponential backoff, honors `Retry-After`); other 4xx raise `DeadlockAPIError`
  with `.status`. Match metadata 404/422 raises `MetadataNotReady` — matches take
  minutes to become queryable after they end, so `fetch`/`watch` must tolerate it.
- The rank `badge` field is already `tier*10 + subrank` (the 0–116 scale the
  analytics `*_average_badge` filters use); `PlayerRank.average_badge` handles this.
- Tests mock HTTP with `respx`; no network in the suite. Live checks are manual
  (the smoke-test command above).
- Commit message trailer used in this repo:
  `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`
