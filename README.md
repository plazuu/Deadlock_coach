# Limpet

A local, single-user AI coach for [Deadlock](https://store.steampowered.com/app/1422450/).

Limpet watches your match history via the [deadlock-api.com](https://deadlock-api.com)
community API, pulls each game's data, extracts a fixed set of performance
features, benchmarks them against players at your rank, and asks Claude to write a
coaching report. It tracks recurring weaknesses across games so the feedback
compounds over time.

See [`docs/PLAN.md`](docs/PLAN.md) for the full design and roadmap.

## Status

Early development. Implemented so far (Phase 0):

- deadlock-api.com client with rate limiting + retry
- Steam ID resolution, static-asset cache (heroes / items / ranks)
- SQLite store, match-history sync, raw metadata fetch
- CLI: `limpet init`, `whoami`, `sync`, `matches`, `fetch`, `assets refresh`

## Quick start

```sh
make install         # creates .venv, installs the package + dev deps
make run ARGS="init"  # prompts for your Steam ID (+ optional API keys)
make run ARGS="sync"  # pull match history
make run ARGS="matches"
```

`make help` lists every target (`test`, `lint`, `fmt`, `check`, `clean`, …).
Without `make`, use a venv directly: `python -m venv .venv && . .venv/bin/activate
&& pip install -e ".[dev]"`, then call `limpet` straight.

Local state (config, database, cached payloads, reports) lives under the platform
data directory, or `LIMPET_DATA_DIR` if set.

## Docker

The container takes all configuration from the environment, so no interactive
`init` is needed.

```sh
cp .env.example .env          # fill in LIMPET_STEAM_ID (+ optional keys)
make docker-build
make docker-run ARGS="sync"
make docker-run ARGS="matches --limit 10"
```

State persists in the `limpet-data` volume (mounted at `/data`). Once the watch
poller lands (Phase 5), `make up` will run it as a long-lived service via
`docker-compose.yml`.
