# Limpet — Deadlock AI Coach

A local, single-user tool that watches your Deadlock match history, pulls each
game's data from the community API (plus replay files where needed), extracts a
fixed set of performance features, benchmarks them against players at your rank,
and asks Claude to write a coaching report. It tracks recurring weaknesses across
games so the feedback compounds over time.

- **Stack:** Python 3.12+
- **Data:** `deadlock-api.com` REST API (match metadata + analytics + assets) and,
  in a later phase, Valve `.dem` replay files
- **Output:** per-match Markdown report + a running progress log
- **Mode:** `limpet watch` auto-detects new matches on your Steam account and runs
  the pipeline unattended
- **Scope:** just your account, identified by Steam ID in config

---

## 1. Data sources

### 1.1 deadlock-api.com (primary)

Community-run API. **First implementation task: fetch the live OpenAPI spec from
`https://api.deadlock-api.com/docs` and pin exact paths / auth / rate limits** —
the paths below are the expected shape, not verified contracts.

| Need | Endpoint (expected) | Notes |
|---|---|---|
| Recent matches for a player | `GET /v1/players/{account_id}/match-history` | Returns `match_id`, hero, result, timestamps. Drives the poller. |
| Full match data | `GET /v1/matches/{match_id}/metadata` | Protobuf-derived JSON: per-player scoreboard, item purchases w/ timestamps, ability points, **net-worth & XP samples over time**, death events, objective events (towers/walkers/shrines/Patron), sampled player positions, damage matrices. This is the workhorse — very rich even without replays. |
| Rank / MMR estimate | `GET /v1/players/{account_id}/mmr-history` (or similar) | Needed to pick the correct benchmark bracket. |
| Aggregate benchmarks | `GET /v1/analytics/...` | Hero win rates, item win rates, and **stat distributions by rank bracket** — the ground truth for "is 480 souls/min at 10:00 good for this hero at Archon?" |
| Assets (names, metadata) | `https://assets.deadlock-api.com/v2/heroes`, `/v2/items`, `/v2/abilities`, `/v2/ranks` | Static lookup tables. Cache locally, refresh weekly. IDs in metadata are meaningless without these. |

Notes / constraints:
- A free API key (from the deadlock-api Discord) is likely required for the
  heavier endpoints. Store it in config; send per their docs.
- Rate limits apply — the client needs a token-bucket limiter + retry/backoff.
- **Metadata ingestion lag:** after a match ends it takes minutes (sometimes
  longer) before metadata is queryable. The fetch step must retry-until-available.
- Deadlock is in active development; schemas drift. Keep normalization thin and
  defensive, and **always persist the raw JSON** so features can be re-derived
  later without re-fetching.

### 1.2 Replay data — Phase 6 (revised after API verification)

`match_info` already contains time-sampled per-player stats, the full item
timeline, and **death positions** (`death_details[].death_pos` / `killer_pos`),
so Phases 1–5 need no replay data at all.

When finer signal is wanted (exact ability casts, per-instance damage, full
movement traces), **use the hosted demo-query API — do not build a parser**:

- `POST /v1/matches/demo/query` with `{match_id, query: "<SQL>"}` runs SQL over
  the demo's entity/event tables and returns a job id.
- Poll `GET /v1/matches/demo/query/{job_id}` for the result artifact.
- `GET /v1/matches/demo/schema?match_id=…` lists the queryable tables/columns.

`limpet.api.client.DeadlockClient.demo_query()` already wraps the submit +
poll loop. Salts are fetched server-side on demand (rate limited).

---

## 2. Architecture

```
limpet/
  pyproject.toml
  src/limpet/
    config.py            # Steam ID, API key, paths, model settings (pydantic-settings)
    ids.py               # SteamID64 <-> account_id (SteamID3) conversion

    api/
      client.py          # deadlock-api.com HTTP client: rate limiting, retry, caching
      matches.py         # match-history, match metadata
      analytics.py       # benchmark distributions by hero + rank bracket
      assets.py          # hero/item/ability/rank lookup tables (cached on disk)

    ingest/
      poller.py          # watch loop: diff match-history vs DB, enqueue new match_ids
      fetch.py           # fetch + persist raw metadata; (Phase 6) download .dem

    parse/
      metadata.py        # raw metadata JSON -> typed models (our player slot resolved)
      demo.py            # Phase 6: drive the parser binary, fold events into features

    features/
      laning.py          # last-hit/deny share, lane state @ 10:00, harass taken/dealt
      economy.py         # souls/min, net-worth curve vs benchmark, key item timings
      combat.py          # KDA-in-context, death analysis, damage & healing share,
                         #   teamfight participation %
      objectives.py      # tower/walker/shrine timings, rotations, jungle efficiency
      positioning.py     # Phase 6: overextension, map coverage, isolation deaths
      benchmarks.py      # attach rank-bracket percentiles to each numeric feature
      extract.py         # orchestrator -> one MatchFeatures record

    coach/
      briefing.py        # compact structured match briefing for the LLM (token-bounded)
      prompt.py          # coaching system prompt + rubric + output schema
      analyze.py         # Anthropic call -> structured CoachingReport
      focus.py           # reconcile new mistakes with tracked focus areas; progress deltas

    report/
      markdown.py        # render per-match report
      progress.py        # render / update the running progress log + trend metrics

    store/
      db.py              # SQLite (sqlite-utils / SQLModel): schema + queries
      files.py           # raw JSON and .dem cache on disk (content-addressed)

    cli.py               # Typer app
```

### CLI surface

| Command | Does |
|---|---|
| `limpet init` | Write config, resolve Steam ID -> account id, cache assets, verify API access |
| `limpet watch` | Long-running poller: detect new matches, run pipeline, write reports, notify |
| `limpet analyze <match_id>` | Run the full pipeline for one match on demand |
| `limpet backfill --last 50` | Ingest + analyze recent history (uses Batch API for the LLM step) |
| `limpet report <match_id>` | Re-render a report from stored data (no re-fetch, no LLM) |
| `limpet progress` | Show the running progress log and trend metrics |

---

## 3. Data model (SQLite)

| Table | Key columns |
|---|---|
| `matches` | `match_id` PK, `played_at`, `hero_id`, `result`, `duration_s`, `rank_bracket`, `raw_meta_path`, `demo_path`, `ingested_at`, `analyzed_at` |
| `match_features` | `match_id` PK, `features_json` (all numeric/categorical features), `benchmarks_json` (percentile for each) |
| `reports` | `id` PK, `match_id`, `model`, `created_at`, `markdown`, `structured_json` |
| `focus_areas` | `id` PK, `theme` (e.g. "dying to ganks before 10:00"), `status` (`active` / `improving` / `resolved`), `first_seen_match`, `last_seen_match`, `evidence_match_ids` |
| `progress_snapshots` | `date` PK, `metrics_json` (rolling souls/min, deaths pre-10, objective participation, benchmark-percentile averages, …) |

Raw payloads live on disk under `~/.limpet/cache/`, referenced by path from the DB.

---

## 4. Per-match pipeline

1. **Ingest** — `fetch.py` pulls metadata JSON (retry until available), stores raw,
   inserts a `matches` row. Phase 6: also download the `.dem`.
2. **Parse** — `parse/metadata.py` resolves *your* player slot and produces typed
   models (scoreboard, timelines, events, purchases).
3. **Feature extraction** — `features/extract.py` computes a **fixed** feature set
   across laning / economy / combat / objectives. Deterministic, unit-tested.
4. **Benchmarking** — `features/benchmarks.py` fetches the analytics distribution
   for `(hero_id, rank_bracket)` and attaches a percentile to every numeric
   feature. Feedback is grounded in "players at your level", not pro play.
5. **Briefing assembly** — `coach/briefing.py` builds a token-bounded (~4–12k
   tokens) structured briefing: final scoreboard line, net-worth curve as
   1-minute samples, ranked event timeline (each death with killer + soul swing,
   objectives, key item buys), lane-phase summary, the biggest benchmark deltas
   in plain language, and **the currently active focus areas from past games**.
   Raw metadata (can be MBs) is never sent to the model.
6. **LLM analysis** — `coach/analyze.py` calls Claude (`claude-opus-5`, adaptive
   thinking) with a structured-output schema:
   - `summary`
   - `did_well[]`
   - `key_mistakes[]` — `{ timestamp, what_happened, why_it_mattered, what_to_do_instead }`
   - `focus_this_week[]` — 1–3 items
   - `drills[]` — concrete practice tasks
   - `progress_note` — how you did on prior focus areas this game
   The system prompt (rubric + schema + persona) is **prompt-cached**; the
   per-match briefing goes after the cache breakpoint.
7. **Longitudinal update** — `coach/focus.py` reconciles new `key_mistakes`
   against `focus_areas` (LLM-assisted clustering, run over the batch), updates
   statuses (`active` → `improving` → `resolved`), and writes a
   `progress_snapshots` row with rolling metrics.
8. **Render** — `report/markdown.py` writes `reports/<played_at>_<match_id>.md`;
   `report/progress.py` updates `PROGRESS.md` with trend arrows.

### Auto-watch loop (`limpet watch`)

- Poll `match-history` every N minutes (configurable, default 10).
- Diff against `matches`; enqueue new `match_id`s; persist last-seen id/timestamp.
- Run the pipeline per match; on metadata-not-ready, requeue with backoff.
- On completion: desktop notification + append a one-line entry to a daily digest
  file (`~/.limpet/digests/YYYY-MM-DD.md`).
- Respect rate limits; single-flight; survive restarts (queue state in SQLite).

---

## 5. LLM usage notes

- **Model:** `claude-opus-5` for the coaching analysis. Adaptive thinking on.
  Structured outputs via `output_config.format`.
- **Cheap path:** the focus-area clustering / dedup step can run on
  `claude-haiku-4-5`, batched.
- **Backfill:** use the Message Batches API (50% cost) for `limpet backfill`.
- **Don't trust the model's Deadlock knowledge.** Hero/item/ability facts change
  constantly and post-date the training cutoff. Put the relevant facts (from the
  assets API) *into the briefing* and instruct the model to reason from the
  supplied numbers and event log, not from memory.
- **Token budget:** target briefing ≤ 12k tokens; hard-cap and log if a match
  would exceed it rather than silently truncating.
- Auth: use `ant auth` profile or `ANTHROPIC_API_KEY` (see the claude-api skill).

---

## 6. Phases

| Phase | Deliverable |
|---|---|
| **0 — Scaffold** | `pyproject.toml`, `config.py`, `ids.py`, deadlock-api client with rate limiting, pull & pin the OpenAPI spec, cache assets, `limpet init`. Fetch one real match's metadata to `cache/` and eyeball it. |
| **1 — Features** | `parse/metadata.py` + `features/` for laning/economy/combat/objectives. SQLite store. `limpet analyze` produces a features JSON (no LLM yet). Fixtures from real matches; unit tests. |
| **2 — Benchmarks** | `analytics.py` + `features/benchmarks.py`. Every numeric feature gets a rank-bracket percentile. |
| **3 — Coach** | `coach/briefing.py`, `prompt.py`, `analyze.py`; `report/markdown.py`. `limpet analyze <match_id>` writes a full Markdown coaching report. |
| **4 — Longitudinal** | `focus_areas` + `progress_snapshots`, `coach/focus.py`, `report/progress.py`, `limpet progress`, `limpet backfill`. |
| **5 — Auto-watch** | `ingest/poller.py`, `limpet watch`, notifications + daily digest, restart-safe queue. |
| **6 — Replays (stretch)** | Vendor a `.dem` parser binary, `parse/demo.py`, `features/positioning.py`; fold positional signals into the briefing. |

---

## 7. Key risks & mitigations

| Risk | Mitigation |
|---|---|
| deadlock-api paths / auth / limits unverified | Phase 0 pulls the live OpenAPI spec and pins everything; client isolates all API shape behind `api/`. |
| Metadata schema drift (game in active dev) | Thin, defensive normalization; always keep raw JSON; features re-derivable offline. |
| Metadata not immediately available post-match | Retry-until-available with backoff in `fetch.py`; poller requeues. |
| No pure-Python Source 2 demo parser | Deferred to Phase 6; subprocess a vendored Rust/Go binary emitting JSONL. |
| LLM hallucinating current Deadlock mechanics | Facts supplied in-briefing from assets API; prompt forbids reasoning from memory. |
| Briefing too large / expensive | Token-bounded assembly with a hard cap and a log line; net-worth curve downsampled to 1-min. |
| Benchmarks vs wrong skill bracket | Resolve rank from mmr-history per match; store `rank_bracket` on the row. |

---

## 8. Testing

- **Fixtures:** record real API responses (VCR-style) for 3–5 matches across
  different heroes/outcomes; commit as test data.
- **Feature extraction:** unit tests asserting known values from those matches.
- **Report rendering:** snapshot tests.
- **Coach:** a small eval set of hand-labelled matches ("this game the main
  mistake was X") to check the report surfaces the right primary issue; run on
  model changes.
