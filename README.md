# Limpet

A local, single-user AI coach for [Deadlock](https://store.steampowered.com/app/1422450/).

Limpet watches your match history via the [deadlock-api.com](https://deadlock-api.com)
community API, pulls each game's data, extracts a fixed set of performance
features, benchmarks them against players at your rank, and asks Claude to write a
coaching report. It tracks recurring weaknesses across games so the feedback
compounds over time.

See [`docs/PLAN.md`](docs/PLAN.md) for the full design and roadmap.

## Status

Phases 0–4 done: ingestion, micro/macro feature extraction, rank-bracket
benchmarks, Claude-written coaching reports, and the long-term memory loop
(tracked focus areas + a regenerated `PROGRESS.md`). Not yet built: the
`watch` auto-poller and replay-derived features. See
[`docs/PLAN.md`](docs/PLAN.md) for the full roadmap and what's left.

## Quick start

```sh
make install                # creates .venv, installs the package + dev deps
make run ARGS="init"         # prompts for your Steam ID (+ optional API keys)
make run ARGS="sync"         # pull match history into the local db
make run ARGS="matches"      # list recent games
make run ARGS="analyze"      # features + benchmarks + a coaching report for your most recent match
make run ARGS="progress"     # show the running coaching profile
make run ARGS="help"         # list every command and what it does
```

`analyze` takes an optional match id (`make run ARGS="analyze <match_id>"`) —
omit it to analyze your most recent match. Run `limpet help` (or
`make run ARGS="help"`) any time for the full command list with descriptions,
or `limpet <command> --help` for a specific command's options.

**Just played a match and it's not showing up in `sync`/`matches`/`analyze`?**
The deadlock-api.com match-history list is server-side cached — it doesn't
always include a match the moment it ends. Pass `--force-refetch` to
`sync`, `matches`, or `analyze` to ask the API to refresh it from Steam.
That's rate-limited to roughly 1-10 requests/hour, so it's meant for
occasional use ("did my last game post yet?"), not every run.

`analyze` needs an Anthropic API key to produce the actual coaching report —
export `ANTHROPIC_API_KEY` in your shell (or `LIMPET_ANTHROPIC_API_KEY` /
`limpet init --anthropic-api-key ...` to store it in config instead). Without
one, pass `--no-report` to stop after features/benchmarks, or `analyze` will
just print an error and keep what it already computed.

`make help` lists every target (`test`, `lint`, `fmt`, `check`, `clean`, …).
Without `make`, use a venv directly: `python -m venv .venv && . .venv/bin/activate
&& pip install -e ".[dev]"`, then call `limpet` straight.

Local state (config, database, cached payloads, reports, `PROGRESS.md`) lives
under the platform data directory, or `LIMPET_DATA_DIR` if set.

## Docker

The container takes all configuration from the environment, so no interactive
`init` is needed.

```sh
cp .env.example .env          # fill in LIMPET_STEAM_ID + LIMPET_ANTHROPIC_API_KEY
make docker-build
make docker-run ARGS="sync"
make docker-run ARGS="analyze"        # omit the match id for your most recent match
make docker-run ARGS="help"           # list every command and what it does
```

State persists in the `limpet-data` volume (mounted at `/data`). Once the watch
poller lands (Phase 5), `make up` will run it as a long-lived service via
`docker-compose.yml`.
