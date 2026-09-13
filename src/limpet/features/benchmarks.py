"""Phase 2 — attach rank-bracket benchmark percentiles to feature leaves.

`GET /v1/analytics/player-stats/metrics` returns, per requested stat name, a
distribution already summarised as `{avg, std, percentile1, percentile5,
percentile10, percentile25, percentile50, percentile75, percentile90,
percentile95, percentile99}` for the matches matching the filter (here: this
hero, this rank bracket). We estimate where our value falls by piecewise-linear
interpolation between those known percentile points — not a guess, but not
exact either; treat it as "roughly around the Nth percentile."

Only leaves with a genuine semantic match to one of the API's tracked stats get
a percentile (`LEAF_TO_STAT` below). Forcing a percentile onto a custom derived
ratio the API doesn't track (`cs_efficiency`, `ability_kill_share`, ...) would
be guessing, not benchmarking — those leaves are left alone.
"""

from __future__ import annotations

from typing import Any

from ..api.client import DeadlockClient

# (dimension, sub_dimension, leaf key) -> deadlock-api analytics stat name.
LEAF_TO_STAT: dict[tuple[str, str, str], str] = {
    ("micro", "last_hitting", "last_hits_total"): "last_hits",
    ("micro", "last_hitting", "denies_total"): "denies",
    ("micro", "aim", "accuracy"): "accuracy",
    ("micro", "aim", "crit_rate"): "crit_shot_rate",
    ("micro", "survival", "damage_taken_per_min"): "player_damage_taken_per_min",
    ("micro", "survival", "self_healing_per_min"): "self_healing_per_min",
    ("macro", "economy", "souls_per_min"): "net_worth_per_min",
    ("macro", "awareness", "deaths_total"): "deaths",
}

_PERCENTILE_STEPS = (1, 5, 10, 25, 50, 75, 90, 95, 99)


def estimate_percentile(value: float, distribution: dict[str, Any]) -> float | None:
    """Where `value` falls in a `{percentileN: ...}` distribution, 0-100."""
    points = [
        (p, distribution.get(f"percentile{p}"))
        for p in _PERCENTILE_STEPS
        if distribution.get(f"percentile{p}") is not None
    ]
    if len(points) < 2:
        return None

    lo_p, lo_v = points[0]
    if value <= lo_v:
        return float(lo_p)
    hi_p, hi_v = points[-1]
    if value >= hi_v:
        return float(hi_p)

    for (p_lo, v_lo), (p_hi, v_hi) in zip(points, points[1:], strict=False):
        if v_lo <= value <= v_hi:
            if v_hi == v_lo:
                return float(p_lo)
            frac = (value - v_lo) / (v_hi - v_lo)
            return p_lo + frac * (p_hi - p_lo)
    return None


def fetch_hero_distributions(
    client: DeadlockClient,
    hero_id: int,
    average_badge: int,
    *,
    badge_window: int = 6,
    max_matches: int = 20_000,
) -> dict[str, dict[str, Any]]:
    """Benchmark distributions for this hero, centred on our rank bracket.

    `average_badge` of 0 (unranked / unknown) skips the badge filter entirely
    rather than querying a misleading "badge 0" bracket.
    """
    params: dict[str, Any] = {"hero_ids": str(hero_id), "max_matches": max_matches}
    if average_badge:
        params["min_average_badge"] = max(0, average_badge - badge_window)
        params["max_average_badge"] = average_badge + badge_window
    return client.player_stats_metrics(**params)


def attach(features_dict: dict[str, Any], distributions: dict[str, dict[str, Any]]) -> None:
    """Mutate `features_dict` (a `MatchFeatures.to_dict()` result) in place,
    adding `benchmark_percentile` to every leaf with a mapped, matching stat."""
    for (dimension, sub, key), stat_name in LEAF_TO_STAT.items():
        leaf = features_dict.get(dimension, {}).get(sub, {}).get(key)
        if not leaf or leaf.get("value") is None:
            continue
        dist = distributions.get(stat_name)
        if not dist:
            continue
        pct = estimate_percentile(leaf["value"], dist)
        if pct is not None:
            leaf["benchmark_percentile"] = round(pct, 1)
