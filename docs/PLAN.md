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

  coach/
    briefing.py        ✅ token-bounded structured briefing (never raw metadata)
    prompt.py          ✅ micro/macro rubric + persona (see §5a on active_focus_areas)
    schema.py          ✅ CoachingReport (pydantic) + the hand-inlined REPORT_JSON_SCHEMA
    analyze.py         ✅ Anthropic call -> CoachingReport; CoachError on any failure
    focus.py           ✅ Haiku reconciles this match's report against tracked
                       focus_areas -> active/improving/resolved + new rows

  report/
    markdown.py        ✅ Summary -> Micro -> Macro -> Focus this week -> Progress
    progress.py        ✅ regenerate PROGRESS.md in full from focus_areas +
                       progress_snapshots (recomputed for today, each run)
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
| `limpet analyze <id>` | ✅ | Parse, compute `{micro,macro}` features, attach benchmark percentiles, call Claude for a coaching report, render + save the Markdown. `--no-report` stops after features/benchmarks (no Anthropic call). |
| `limpet progress` | ✅ | Show the coaching profile (`PROGRESS.md`): active/resolved focus areas, last game's strengths, the daily trend table |
| `limpet backfill --last N` | not built | Ingest + analyze recent history (Batch API) — would also backfill historical `progress_snapshots` days, which `analyze` alone only computes for "today" |
| `limpet report <id>` | not built | Re-render from stored data (no re-fetch, no LLM) |
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
7. **Longitudinal** (`coach/focus.py`) — with nothing tracked yet, every
   `focus_this_week` item just opens a new `focus_areas` row (no LLM call).
   Otherwise a single `claude-haiku-4-5` call gets every tracked focus area
   plus this match's mistakes/focus_this_week/progress_note and returns, for
   *every* tracked item, `active` / `improving` / `resolved` (biased toward
   `active` when unsure — one clean game isn't proof), plus any genuinely new
   themes to start tracking. `db.update_focus_area_status` appends the match id
   as evidence; an item the model doesn't return is left untouched rather than
   guessed at.
8. **Render** (`report/`) — `reports/<played_at>_<match_id>.md` with Summary →
   Micro → Macro → Focus this week → Progress; `report/progress.py` recomputes
   *today's* `progress_snapshots` row (matches/reports already on file for
   today, idempotent) and rewrites `PROGRESS.md` in full from `focus_areas` +
   `progress_snapshots` — never incrementally patched.

### Auto-watch loop (`limpet watch`, Phase 5)

Poll `match-history` every N min; diff against `matches`; enqueue new ids; run the
pipeline; on `MetadataNotReady` requeue with backoff. On completion: desktop
notification + a line in `digests/YYYY-MM-DD.md`. Restart-safe (queue in SQLite).

---

## 5a. Long-term memory: the coaching profile

This is what makes it a *coach* rather than a per-match report generator: it
needs to remember what it already told you and check back on it. Two layers,
same "raw/derived" split the rest of the codebase already uses (CLAUDE.md
§Source-of-truth rule) — never one file trying to be both:

1. **SQLite is the durable, structured store — the only thing ever written to.**
   `focus_areas` (one row per recurring theme, `dimension` micro/macro, `status`
   `active → improving → resolved`, `evidence_match_ids`) and
   `progress_snapshots` (rolling metrics per day). Queryable, joinable to
   specific matches, safe to update from every `analyze` run.
2. **`PROGRESS.md` is a rendered *view*, regenerated from SQLite after every
   match — never hand-edited, never the thing anything reads state from.** Its
   job is to be the compact context that goes back into the *next* match's
   briefing (so the model has continuity) and the page you open yourself to see
   "how am I doing." Same reason this repo keeps raw API payloads as the source
   of truth and treats the DB as derived — and structurally the same trick
   CLAUDE.md/MEMORY.md use for *my* memory: a durable store underneath, a
   compact rendered summary that actually gets read each time.

The loop, tying together pipeline steps 5–8 above:

- **Before analyzing** a match, `coach/briefing.py` pulls current `active`/
  `improving` focus areas straight from SQLite (not the .md) and puts a short
  summary in the briefing: *"Focus areas coming into this game: [2 micro, 1
  macro]."*
- **The LLM's `progress_note`** (step 6) is Claude's read on whether this match
  supports each open focus area improving, stalling, or resolved.
- **`coach/focus.py`** (step 7) reconciles this match's report against
  existing `focus_areas` with one cheap Haiku call (skipped entirely if
  nothing is tracked yet), advances or opens rows.
- **`report/progress.py`** (step 8) re-renders `PROGRESS.md` in full from the
  updated tables — it's a full regeneration each time, not an append, so it
  never drifts from what SQLite actually holds.

Actual rendered output (real match, real player, from a live run):

```markdown
# Deadlock Progress

## Active focus

- **[macro] Fight participation and objective conversion** — improving, seen in 2 game(s) since match 104738184
- **[micro] Last-hit conversion** — active, seen in 2 game(s) since match 104738184
- **[micro] Shot discipline** — active, seen in 2 game(s) since match 104738184
- **[macro] Tempo from a safe lane** — active, seen in 1 game(s) since match 103317079
- **[macro] Risk management / overcorrection** — active, seen in 1 game(s) since match 103317079

## Recently resolved

_Nothing resolved yet._

## Solid, as of the last game

- 97.7th-percentile crit rate (20.1%) — your burst windows are landing hard
- 100% of your kills involved an ability — your kit is doing the closing work
- 13 objectives taken vs 8 lost, and 2-1 on boss objectives

## Trend (daily, most recent first)

| day | matches | micro avg | macro avg | benchmark %ile avg |
|---|---|---|---|---|
| 2026-09-12 | 2 | 66.5 | 44.0 | 51.2 |
```

One real limitation of this design, found while verifying it live:
`report/progress.py`'s snapshot recompute only ever touches *today*'s row
(cheap — no need to rescan every historical day on every `analyze` call). A
match played days or weeks ago and analyzed just now — the normal case while
there's no `backfill`/`watch` yet — contributes to `focus_areas` immediately
but won't show up in the Trend table until a *historical* snapshot is
backfilled (not built) or a match is actually analyzed on the day it's
played. Not a bug, just a gap the not-yet-built `backfill` command should
close by recomputing every day it touches, not just today's.

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
| **3 — Coach** ✅ | `coach/{briefing,prompt,schema,analyze}.py`, `report/markdown.py`. `limpet analyze <id>` writes the Summary/Micro/Macro/Focus/Progress Markdown report. Schema is hand-inlined JSON (no `$ref`/`$defs`) validated against `CoachingReport`; `active_focus_areas` in the briefing and `db.active_focus_areas()` are wired but empty until Phase 4 populates them. Unit-tested against a fake Anthropic client — no real API calls in the test suite. |
| **4 — Longitudinal** ✅ | `coach/focus.py` (Haiku reconciliation against `focus_areas`), `report/progress.py` (recompute today's `progress_snapshots` row + full re-render), `limpet progress`. Verified live: a real match opened 3 focus areas, a second real match advanced one to `improving` and opened 2 more. `limpet backfill` / `limpet report` (re-render without re-analyzing) not built — see the Trend-table gap noted in §5a. |
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
