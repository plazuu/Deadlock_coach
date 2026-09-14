# Future improvement notes

Running list of small, not-yet-scoped improvement ideas. Unlike `PLAN.md`
(the phased roadmap), this is a scratch list — promote an item into `PLAN.md`
once it's actually being built.

## `whoami` — more identity context

- **Top 3 most-played heroes — shipped.** `db.top_heroes()` groups the local
  `matches` table by `hero_id`; `whoami` prints it via `Assets.hero_name()`.
  Empty/absent gracefully before a first `sync`.
- **Current Steam username (persona name) — still open, blocked.** Not
  available from deadlock-api.com (checked `api-notes.md` and the live
  `openapi.json` — no such field anywhere). Needs Steam's own Web API
  (`ISteamUser/GetPlayerSummaries`), a new key, and a small second client.
  Explicitly skipped per the user's call (no Steam key configured) — revisit
  if one gets added to `.env`.

## `analyze` supports "last game" with no match id — shipped

`match_id` is now `int | None = None` on `limpet analyze`; when omitted it
calls `client.match_history(account_id)` (same call `matches` makes), takes
the entry with the latest `start_time`, and proceeds exactly as before —
`MetadataNotReady` still surfaces the "try again in a few minutes" message if
the most recent match isn't queryable yet.

## Positional macro coaching

Confirmed and used: `match_info.match_paths` is real, continuous per-player
position data (`MatchView.match_paths` / `.position_at()` in
`parse/metadata.py`) — no replay/demo-query needed. Two things shipped on
top of it:

- **`rotations.py`'s `avg_response_distance` — shipped**, replacing a stale
  `needs_demo` stub. Now the real average distance from *our* position to
  each *teammate's* death location, at the moment they died.
- **Farm-stealing heuristic — shipped as v1**, see below.

**Naming the specific structure ("walker") — SOLVED, but held pending `watch` (Phase 5).**

`match_info.objectives[]`'s `team_objective_id` itself is still unmapped —
but it turns out that's the wrong data source to solve this from. The demo-
query API's `BossKilledEvent` table is a complete, self-sufficient record of
every structure kill: `entity_killed_class`, `entity_position` (real x/y/z),
`objective_team`, `gametime`. Verified against a real match
(`tests/fixtures/match_104887482.json`'s match_id, cross-referenced against
its own `objectives[]` via `objective_team = team + 2` and
`gametime ≈ destroyed_time_s + 54.5s`, purely to validate the mapping — not
needed in production, see below) and by decoding `entity_killed` (a packed
Source-engine handle: `entity_killed & 0x3FFF` = `entity_index`) against the
demo's per-class snapshot tables:

| `entity_killed_class` | table | structure |
|---|---|---|
| 5 | `CNPC_TrooperBoss` | tier1 guardian |
| 29 | `CNPC_Boss_Tier2` | **walker** |
| 30 | `CNPC_BarrackBoss` | base guardian |
| 31 | `CNPC_Boss_Tier3` | shrine |
| 28 | `CCitadel_Destroyable_Building` | likely patron shield/phase-gate (by elimination against `objective_params`'s 5 named categories — least certain of the five) |

This is a fixed game constant (compiled into the class tables, not a
per-match statistical pattern), so it doesn't need re-validation across many
matches the way a heuristic would. Production code wouldn't need the
`objectives[]`/`team_objective_id` alignment at all — `BossKilledEvent`
alone gives team, tier, exact position, and time directly.

**Why this is held rather than built now:** Valve's replay files have a
short retention window. Checked `GET /v1/matches/{id}/salts` (no metadata
quota cost) across six other real matches from the same account, spanning
roughly 1–40 hours old — every one already had `replay_salt: null` /
`demo_url: null`, meaning the demo is gone server-side. Only the original
fixture match still had one available. So this feature can only ever work
on matches analyzed *shortly after they're played* — which means it's
mostly unusable until `limpet watch` (Phase 5, not yet built) exists to
catch matches in that window. Explicitly holding until then per the user's
call, rather than shipping something that degrades to useless for anyone
analyzing matches even a day later.

When `watch` lands: wire a `demo_query(match_id, "SELECT * FROM
BossKilledEvent")` call (already available via `DeadlockClient.demo_query()`)
into the pipeline, resolve `entity_killed_class` via the table above, and
feed `{team, tier, position, t}` per destroyed structure into the briefing
similarly to how `coach/builds.py` added `item_builds` — cross-reference
against `MatchView.position_at()` (already shipped) to say who was where
when it fell. Expect ~60-90s added latency per match the first time its demo
is queried (full replay parse) — this should be an explicit, cached step,
not a silent addition to every `analyze` call.

## Farm-stealing heuristic — shipped as v1 (`features/macro/farm_stealing.py`)

Self-referential heuristic, no external map/lane mapping needed (see the
blocked item above for why that matters): each teammate death opens a
45s (`WINDOW_S`) window; count it "taken" if our position moved closer to
that teammate's own lane-phase zone (centroid of *their* position samples
during the first 10 minutes) than to our own, and our net-worth rate in that
window beat our match-average rate by 1.3x (`RATE_MULTIPLIER`). Both
constants are first-pass and undocumented by any spec — expect to retune
after checking this against real matches. Degrades to `needs_demo` if
`player_slot`/`match_paths` are missing.

Considered and rejected: sourcing pro/tournament games as a stronger
"ground truth" than rank-bracket percentiles for farm-stealing timing.
deadlock-api.com has no pro/tournament tagging to even identify those match
ids, and pro-level timing depends on power spikes/coordination a lower-rank
player doesn't have — same-rank percentile benchmarking (`benchmarks.py`)
stays the more defensible source of truth. Not revisiting unless a pro-data
source surfaces independently.

## Itemization critique names actual items — ours AND the enemy's — shipped

New `coach/builds.py`: `build_item_context(view, account_id, assets)` returns
`{our_build: [{t, item}], enemies: [{hero, build: [{t, item}]}]}` — our full
resolved purchase order, and each enemy's hero + notable (tier2+) purchases,
capped that way to keep the briefing compact across up to 6 enemies. Wired
into `briefing.build()` as a new top-level `item_builds` key (sibling to
`active_focus_areas`, not a `Leaf` — it doesn't reduce to a benchmarkable
scalar), and into the system prompt so the coach can say "the enemy Vindicta
had X by 9:00 — you needed Y before that" instead of speaking only in
aggregate counts.
