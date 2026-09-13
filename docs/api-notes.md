# deadlock-api.com — verified notes

Captured from the live OpenAPI spec and real responses on 2026-09-10. The API
version string was `0.1.0`; treat everything as subject to change and keep the
raw payloads.

## Auth & limits

- Base URL: `https://api.deadlock-api.com`
- Auth: `X-API-KEY` header (or `?api_key=`). A key is optional but roughly
  doubles rate limits. Get one from the deadlock-api Discord.
- Analytics endpoints share a pool: 200 req/min (IP) / 400 (key) / 2000 (global).
- `match-history` with `force_refetch=true` and the `salts` Steam fallback are
  heavily limited (≈1–10 req/hour) — only used opportunistically.
- **`GET /v1/matches/{id}/metadata` is IP-limited to 3 req/hour without an API
  key** (observed 2026-09-12: `429` with `{"quota":{"limit":3,"period":3600}}`).
  This is much tighter than the analytics pool and hits fast during dev —
  `ingest.cached_metadata()` / `limpet analyze`'s cache-first check exists partly
  because of this. Get a free key (deadlock-api Discord) before doing any bulk
  `fetch`/`analyze` work; re-verify the with-key limit when one is configured.
- Analytics responses are cached server-side 1–6h per unique query.

## Endpoints Limpet uses

| Purpose | Endpoint |
|---|---|
| Match history | `GET /v1/players/{account_id}/match-history` → `PlayerMatchHistoryEntry[]` |
| Full match data | `GET /v1/matches/{match_id}/metadata` → `{match_info, hero_build_ids, pregame_hero_ids, banned_hero_ids}` |
| Bulk / filtered metadata | `GET /v1/matches/metadata` (rich `include_*` toggles, `ndjson` option) |
| Current rank | `GET /v1/players/{account_id}/rank` → `{badge, rank, subrank, last_match}` |
| Salts (for demo file) | `GET /v1/matches/{match_id}/salts` |
| Benchmark quantiles | `GET /v1/analytics/player-stats/metrics` (DDSketch quantiles, filter by `hero_ids`, `min/max_average_badge`) |
| Hero win/pick rates | `GET /v1/analytics/hero-stats` |
| Static assets | `GET /v1/assets/{heroes,items,ranks}` |
| Ad-hoc ClickHouse SQL | `GET /v1/sql?query=…` (+ `/v1/sql/tables`, `/v1/sql/tables/{t}/schema`) |
| Hosted replay analysis | `POST /v1/matches/demo/query` `{match_id, query}` → job; poll `GET /v1/matches/demo/query/{job_id}`; schema at `GET /v1/matches/demo/schema` |

**Note on rank:** `badge` is already `tier*10 + subrank` (e.g. `113` = Eternus 3),
which is the same 0–116 scale the analytics `*_average_badge` filters use.
`badge`/`rank`/`subrank` are all `0` for unranked ("Obscurus").

## `match_info` shape (the workhorse)

Top level: `duration_s`, `match_outcome`, `winning_team`, `start_time`,
`match_id`, `game_mode`, `match_mode`, `average_badge_team0/1`, `objectives`,
`damage_matrix`, `mid_boss`, `match_paths`, `teams`, `match_tracked_stats`,
`custom_user_stats`, `players[]`.

Per player in `players[]`:

- Identity: `account_id`, `player_slot`, `team`, `hero_id`, `assigned_lane`,
  `level`, `mvp_rank`, `player_match_outcome`
- Totals: `kills`, `deaths`, `assists`, `net_worth`, `last_hits`, `denies`,
  `ability_points`
- **`stats[]`** — time-sampled snapshots (`time_stamp_s`), each with:
  `net_worth`, a full `gold_*` breakdown (`gold_lane_creep`, `gold_neutral_creep`,
  `gold_boss`, `gold_denied`, `gold_death_loss`, …), `kills`/`deaths`/`assists`,
  `creep_kills`, `neutral_kills`, **`possible_creeps`** (last-hit potential →
  CS efficiency), `player_damage`, `player_damage_taken`, `player_healing`,
  `self_healing`, `denies`, `level`, `shots_hit`/`shots_missed`,
  `hero_bullets_hit`/`_crit`, `headshot_kills`, `ability_kills`, `bullet_kills`,
  `damage_mitigated`, `heal_prevented`, …
- **`items[]`** — `{game_time_s, item_id, upgrade_id, sold_time_s, flags,
  imbued_ability_id}` — full purchase timeline
- **`death_details[]`** — `{game_time_s, time_to_kill_s, killer_player_slot,
  death_pos{x,y,z}, killer_pos{x,y,z}, death_duration_s}` — positional death
  data **without needing the replay**
- `pings[]` — `{ping_type, ping_data, game_time_s}`
- `ability_stats`, `stats_type_stat`, `accolades`, `player_tracked_stats`,
  `power_up_buffs`, `book_rewards`

### Implications for the plan

- Laning / economy / combat / objectives features are all derivable from
  `match_info` alone — no replay needed for Phases 1–5.
- Basic positioning signal (isolation deaths, death map zones, gank exposure)
  comes from `death_details[].death_pos` + `killer_pos`.
- Phase 6 "replay parsing" is **not** "vendor a Rust parser" — it's the hosted
  `POST /v1/matches/demo/query` SQL API. Update `PLAN.md §1.2` accordingly.
- `GET /v1/sql` against ClickHouse can compute custom rank-bracket benchmarks
  that the fixed analytics endpoints don't expose.
