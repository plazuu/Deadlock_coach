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
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
limpet init          # prompts for your Steam ID (+ optional API keys)
limpet sync          # pull match history
limpet matches       # list recent games
```

Local state (config, database, cached payloads, reports) lives under the platform
data directory, or `LIMPET_DATA_DIR` if set.
