# Limpet — Deadlock AI Coach

A local, single-user tool that watches your Deadlock match history, pulls each
game's data from the community API, extracts a fixed set of performance features,
benchmarks them against players at your rank, and asks Claude to write a coaching
report. It tracks recurring weaknesses across games so the feedback compounds
over time.

- **Stack:** Python 3.12+
- **Data:** [deadlock-api.com](https://api.deadlock-api.com) REST API (match
  metadata + analytics + assets); the hosted demo-query API for replay-level
  detail in a later phase
- **Output:** per-match Markdown report split into **micro** and **macro**, plus a
  running progress log with a trend line for each
- **Mode:** `limpet watch` auto-detects new matches on your Steam account and runs
  the pipeline unattended
- **Scope:** just your account, identified by Steam ID in config

See `docs/api-notes.md` for verified endpoint shapes and the `match_info` schema.

---

## 1. Coaching framework: micro and macro

Everything the coach produces — every feature, observation, focus area, and drill
— is tagged **micro** or **macro**. The report has these two as its top-level
sections and progress tracking carries a separate trend line for each. The split
keeps feedback actionable: the fix for a micro problem is mechanical practice;
the fix for a macro problem is a decision rule.

### Micro — execution in the moment

What you do with your hands and your character in the seconds you're doing it.

| Sub-dimension | Signals from `match_info` | Notes |
|---|---|---|
| Last-hitting & denies | `stats[].creep_kills` / `possible_creeps`, `denies`, `neutral_kills` | CS efficiency = last hits ÷ available. Metadata-only. |
| Weapon accuracy | `shots_hit` / `shots_missed`, `hero_bullets_hit`, `_crit`, `headshot_kills` | Hit % and crit/headshot rate vs benchmark. |
| Ability usage | `ability_stats`, `ability_kills`, `stats[].ability_points` | Landing skillshots, upgrade order. Cast-level timing needs the demo query. |
| Trading & harass | lane-phase `stats[].player_damage` vs `player_damage_taken` | Winning the damage race without overcommitting. |
| Fight execution | per-death `time_to_kill_s`, damage/healing in the fight window, cooldowns unused at death | Dying with your kit in the tank. |
| Survivability | `player_damage_taken` vs benchmark, `damage_mitigated`, isolated deaths from `killer_pos` | Avoidable deaths, dodging, defensive item timing. |

### Macro — decisions across the map and the match

Where you are, what you're doing there, and why — relative to the game state.

| Sub-dimension | Signals | Notes |
|---|---|---|
| Farm routing & economy | `stats[].net_worth` curve, `gold_lane_creep` vs `gold_neutral_creep` vs `gold_boss`, souls/min, post-death downtime | Efficient patterns, not just totals. |
| Wave & lane management | net-worth slope around objective events, assigned lane vs where farm/deaths happen | Freeze/push inference; sharper with the demo query. |
| Rotations & tempo | fight-participation %, arrival timing to `death_details` clusters, roam windows | Being where the game is. |
| Objective control | tower / walker / shrine / mid-boss / Patron timings from `match_info.objectives` + `mid_boss` | Trading objectives, taking them on your timing. |
| Map awareness | deaths far from allies, ganked before 10:00, repeated deaths in one zone (`death_pos` clustering) | Getting caught is macro, not micro. |
| Itemization | `items[]` purchase timeline vs benchmark spike timings; reactive buys vs enemy comp | Timing and adaptation, not the exact build. |
| Risk management | behaviour with a lead vs behind (net-worth delta at death), recall discipline, greeding objectives | Win-condition awareness. |

### Cross-cutting: mental / tilt

Not a third pillar. When a pattern is behavioural — death spiral after one bad
fight, forcing plays while behind, abandoning a farm pattern — the coach attaches
a `mental` flag to the relevant micro or macro item.

The sub-dimension lists are a **rubric given to the model as guidance**, not a
fixed schema. The model may name a more specific theme ("dashing in before your
2 is up") under the right pillar.

---

## 2. Data sources

### 2.1 deadlock-api.com

Base `https://api.deadlock-api.com`, auth via `X-API-KEY` header (optional, ~2×
rate limits). Endpoints in use are wrapped in `limpet.api.client.DeadlockClient`
— see `docs/api-notes.md` for the verified list. Key ones:

| Need | Endpoint |
|---|---|
| Match history (drives the poller) | `GET /v1/players/{account_id}/match-history` |
| Full match data (the workhorse) | `GET /v1/matches/{match_id}/metadata` → `{match_info, hero_build_ids, pregame_hero_ids, …}` |
| Current rank / bracket | `GET /v1/players/{account_id}/rank` (`badge` = the 0–116 `average_badge` scale) |
| Benchmark quantiles | `GET /v1/analytics/player-stats/metrics` (filter by `hero_ids`, `min/max_average_badge`) |
| Hero win/pick rates | `GET /v1/analytics/hero-stats` |
| Static assets | `GET /v1/assets/{heroes,items,ranks}` |
| Ad-hoc benchmarks | `GET /v1/sql?query=…` (ClickHouse) |

Constraints:
- **Metadata ingestion lag** — a match is queryable minutes (sometimes longer)
  after it ends; 404/422 surfaces as `MetadataNotReady` and the caller retries.
- **Schema drift** — Deadlock is in active development. Normalisation stays thin
  and defensive; the raw JSON is always persisted so features re-derive offline.
- Rate limits: client-side min-interval + retry/backoff on 429/5xx.

### 2.2 Replay data — Phase 6

`match_info` already carries time-sampled per-player stats, the full item
timeline, and death/killer **positions**, so Phases 1–5 need no replay data.

For cast-level ability usage, per-instance damage, and precise movement, use the
**hosted demo-query API** (`POST /v1/matches/demo/query` with `{match_id, query}`
→ poll `/v1/matches/demo/query/{job_id}`; schema at `/v1/matches/demo/schema`).
`DeadlockClient.demo_query()` wraps the submit+poll loop. **Do not build a
parser.** Sub-dimensions above marked "needs the demo query" are filled here.

---

## 3. Architecture

Current layout (grows toward the tree below as phases land):

```
src/limpet/
  ids.py               Steam ID resolution (any form -> 32-bit account id)
  paths.py             single filesystem-layout authority (LIMPET_DATA_DIR)
  config.py            pydantic-settings: env LIMPET_* > config.json > defaults
  assets.py            hero/item/rank id->name, disk-cached with TTL

  api/
    client.py          the ONLY file that knows deadlock-api URL shapes
    models.py          typed models for stable responses; metadata stays raw dict

  ingest.py            match-history sync + raw metadata fetch/cache;
                       upsert_from_metadata() derives a `matches` row without a prior sync

  parse/               resolve our player slot; typed, defensive views of match_info
    metadata.py        ✅ MatchView: player()/teammates()/enemies()/average_badge()
    demo.py            [Phase 6] shape demo-query results

  features/            deterministic, unit-tested feature extraction
    common.py          ✅ Leaf record, phase windows, safe_ratio/per_min/euclidean helpers
    micro/             ✅ lasthits.py  aim.py  abilities.py  trades.py  fights.py  survival.py
    macro/             ✅ economy.py  objectives.py  rotations.py  awareness.py  itemization.py  waves.py
    benchmarks.py      ✅ attach rank-bracket percentile to each mapped leaf
    extract.py         ✅ orchestrator -> MatchFeatures { micro: {...}, macro: {...} }

  coach/               [Phase 3]
    briefing.py        token-bounded structured briefing (never raw metadata)
    prompt.py          micro/macro rubric + persona + output schema (prompt-cached)
    analyze.py         Anthropic call -> CoachingReport
    focus.py           [Phase 4] reconcile mistakes with tracked focus areas

  report/              [Phase 3] markdown.py (per-match) ; progress.py (PROGRESS.md)
  store/db.py          SQLite: index + workflow tracker, not source of truth
  cli.py               Typer app
```

Each feature leaf returns `{ value, unit, dimension, sub_dimension }` and, after
Phase 2, `benchmark_percentile`. `MatchFeatures` is `{ micro: {...}, macro: {...} }`.

### CLI surface

| Command | Status | Does |
|---|---|---|
| `limpet init` | ✅ | Write config, resolve Steam ID, cache assets, verify access |
| `limpet whoami` | ✅ | Show resolved account id + current rank |
| `limpet sync` | ✅ | Pull match history into the local db |
| `limpet matches` | ✅ | List recent history |
| `limpet fetch <id>` | ✅ | Fetch + cache one match's metadata |
| `limpet analyze <id>` | ✅ (features + benchmarks) | Parse, compute `{micro,macro}` features, attach rank-bracket benchmark percentiles, save + print JSON. No LLM call yet — that's Phase 3. |
| `limpet backfill --last N` | Phase 4 | Ingest + analyze recent history (Batch API) |
| `limpet report <id>` | Phase 4 | Re-render from stored data (no re-fetch, no LLM) |
| `limpet progress` | Phase 4 | Show micro/macro trend lines + open focus areas |
| `limpet watch` | Phase 5 | Long-running poller |

---

## 4. Data model (SQLite)

`store/db.py`. The database is a derived index — every computed table must
reconstruct from the raw payloads under `<data_dir>/cache/`.

| Table | Notes |
|---|---|
| `matches` | one row per match; `raw_meta_path`, `demo_path`, `average_badge` (our bracket at the time), `ingested_at`, `analyzed_at` |
| `match_features` | `features_json` = `{micro:{…}, macro:{…}}`, `benchmarks_json` = percentile per leaf |
| `reports` | `markdown` + `structured_json` (the CoachingReport), `model`, `created_at` |
| `focus_areas` | `theme`, **`dimension`** (`micro`\|`macro`), `status` (`active`\|`improving`\|`resolved`), `first/last_seen_match`, `evidence_match_ids` |
| `progress_snapshots` | `metrics_json` with `micro` / `macro` sub-objects + `micro_rating` / `macro_rating` rolling averages |

---

## 5. Per-match pipeline

1. **Ingest** (`ingest.py`) — pull `match_info` (retry until available), persist
   raw, upsert the `matches` row.
2. **Parse** (`parse/metadata.py`) — resolve *our* `player_slot`; typed views of
   the scoreboard, `stats[]` timeline, `items[]`, `death_details[]`, objectives.
3. **Feature extraction** (`features/extract.py`) — run every `micro/*` and
   `macro/*` leaf. Deterministic, fixture-tested. Output `{micro, macro}`.
4. **Benchmarking** (`features/benchmarks.py`) — fetch the analytics distribution
   for `(hero_id, average_badge bracket)`; attach a percentile to every numeric
   leaf. Feedback is grounded in "players at your level", not pro play.
5. **Briefing** (`coach/briefing.py`) — token-bounded (~4–12k) structured
   briefing: scoreboard line, net-worth curve at 1-min samples, ranked event
   timeline (deaths with killer + soul swing, objectives, key item buys),
   lane-phase summary, the biggest benchmark deltas **grouped micro vs macro**,
   and the currently active focus areas. Raw metadata is never sent.
6. **LLM analysis** (`coach/analyze.py`) — Claude (`claude-opus-5`, adaptive
   thinking), structured output:
   ```
   summary                       # 2–3 sentences: the single biggest lever
   micro:  { assessment, strengths[], mistakes[], rating_0_100 }
   macro:  { assessment, strengths[], mistakes[], rating_0_100 }
     mistakes[] = { timestamp, theme, what_happened, why_it_mattered, fix, mental? }
   focus_this_week[]             # 1–3, each { dimension, theme, why, drill }
   progress_note                 # how prior micro/macro focus areas went this game
   ```
   Prompt rules: always fill both `micro` and `macro`; if the match was genuinely
   clean on one axis, say so briefly instead of inventing problems. Don't stack
   all of `focus_this_week` on one pillar unless that pillar is clearly the
   bottleneck. `rating` is model-estimated but benchmark-anchored — it feeds the
   trend line, not a grade. System prompt (rubric + schema) is prompt-cached; the
   briefing goes after the breakpoint.
7. **Longitudinal** (`coach/focus.py`) — reconcile new `mistakes` against
   `focus_areas` **within each dimension** (LLM-assisted clustering, batched),
   advance statuses, write a `progress_snapshots` row.
8. **Render** (`report/`) — `reports/<played_at>_<match_id>.md` with Summary →
   Micro → Macro → Progress; `PROGRESS.md` gets the two trend lines and the open
   focus areas grouped by dimension.

### Auto-watch loop (`limpet watch`, Phase 5)

Poll `match-history` every N min; diff against `matches`; enqueue new ids; run the
pipeline; on `MetadataNotReady` requeue with backoff. On completion: desktop
notification + a line in `digests/YYYY-MM-DD.md`. Restart-safe (queue in SQLite).

---

## 6. LLM usage notes

- **Model:** `claude-opus-5`, adaptive thinking, structured outputs via
  `output_config.format`.
- **Cheap path:** focus-area clustering can run on `claude-haiku-4-5`, batched.
- **Backfill:** Message Batches API (50% cost).
- **Don't trust the model's Deadlock knowledge** — it post-dates the training
  cutoff and changes every patch. Put the facts (from the assets API) *in the
  briefing* and instruct the model to reason from the supplied numbers and event
  log, not memory.
- **Token budget:** briefing ≤ `settings.briefing_token_budget` (default 12k);
  hard-cap and log rather than silently truncate.
- Auth: `LIMPET_ANTHROPIC_API_KEY`, else `ANTHROPIC_API_KEY` / `ant auth` profile.

---

## 7. Phases

| Phase | Deliverable |
|---|---|
| **0 — Scaffold** ✅ | API client, config, asset cache, SQLite store, `init/whoami/sync/matches/fetch`. |
| **1 — Features** ✅ | `parse/metadata.py`; `features/micro/*` + `features/macro/*` (metadata-only leaves) + `extract.py`. `limpet analyze` emits a `{micro,macro}` features JSON (no LLM). Fixture match (`tests/fixtures/match_104887482.json`) + unit tests. `waves.py` and part of `rotations.py` are honestly stubbed (`needs_demo: true`) — they need Phase 6. |
| **2 — Benchmarks** ✅ | `features/benchmarks.py`: `GET /v1/analytics/player-stats/metrics` (hero + rank-bracket-windowed) already returns a full percentile breakdown per stat — no quantile math of our own. Only leaves with a genuine 1:1 match to an API-tracked stat get a `benchmark_percentile` (`LEAF_TO_STAT`, 8 leaves); everything else is left alone rather than forced. |
| **3 — Coach** | `coach/{briefing,prompt,analyze}.py`, `report/markdown.py`. `limpet analyze <id>` writes the two-section Markdown report. |
| **4 — Longitudinal** | `focus_areas` (with `dimension`) + `progress_snapshots`, `coach/focus.py`, `report/progress.py`, `limpet progress` / `backfill` / `report`. |
| **5 — Auto-watch** | `limpet watch`: poller, notifications, daily digest, restart-safe queue. |
| **6 — Replays (stretch)** | `parse/demo.py` over the hosted demo-query API; fill the "needs the demo query" leaves (cast-level abilities, wave management, precise rotations). |

---

## 8. Key risks & mitigations

| Risk | Mitigation |
|---|---|
| Metadata schema drift (game in active dev) | Thin defensive parsing; raw JSON always kept; features re-derivable offline; models use `extra="allow"`. |
| Metadata not available right after a match | `MetadataNotReady` + retry/backoff; poller requeues. |
| Micro/macro attribution is fuzzy for some signals (e.g. a death) | The rubric assigns each sub-dimension a home; "getting caught" is macro, "lost the 1v1" is micro. The model gets that guidance explicitly. |
| LLM inventing balance on a clean axis | Prompt: state "solid this game" briefly rather than manufacture mistakes; `focus_this_week` may be 1 item. |
| LLM hallucinating current mechanics | Facts in-briefing from the assets API; prompt forbids reasoning from memory. |
| Briefing too large / expensive | Token-bounded assembly, hard cap + log line, curves downsampled. |
| Benchmarks vs wrong skill bracket | Resolve rank per match, store `average_badge` on the row. |

---

## 9. Testing

- **Fixtures:** commit raw API responses for 3–5 matches across heroes/outcomes.
- **Feature extraction:** unit tests asserting known micro & macro leaf values.
- **Report rendering:** snapshot tests on both sections.
- **Coach:** a small eval set of hand-labelled matches ("this game the main
  problem was macro: farmed a dead lane while mid fell") to check the report puts
  the primary issue in the right pillar; re-run on model/prompt changes.
