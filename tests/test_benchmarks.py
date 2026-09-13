from __future__ import annotations

import httpx
import pytest
import respx

from limpet.api.client import BASE_URL, DeadlockClient
from limpet.features.benchmarks import attach, estimate_percentile, fetch_hero_distributions

DIST = {
    "avg": 500.0,
    "std": 100.0,
    "percentile1": 100.0,
    "percentile5": 200.0,
    "percentile10": 250.0,
    "percentile25": 350.0,
    "percentile50": 500.0,
    "percentile75": 650.0,
    "percentile90": 750.0,
    "percentile95": 800.0,
    "percentile99": 900.0,
}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (500.0, 50.0),  # exactly at the median
        (50.0, 1.0),  # below the lowest known point -> clamp
        (1000.0, 99.0),  # above the highest known point -> clamp
        (300.0, pytest.approx(10 + (300 - 250) / (350 - 250) * (25 - 10))),
    ],
)
def test_estimate_percentile(value, expected):
    assert estimate_percentile(value, DIST) == expected


def test_estimate_percentile_needs_at_least_two_points():
    assert estimate_percentile(500.0, {"percentile50": 500.0}) is None
    assert estimate_percentile(500.0, {}) is None


def test_attach_only_touches_mapped_leaves_with_values():
    ratio_dist = {"percentile1": 0.1, "percentile50": 0.5, "percentile99": 0.9}
    features = {
        "micro": {
            "aim": {
                "accuracy": {"value": 0.5, "unit": "ratio"},
                "crit_rate": {"value": None, "unit": "ratio"},  # no value -> skipped
            },
            "abilities": {
                "ability_kill_share": {"value": 0.8, "unit": "ratio"},  # not in LEAF_TO_STAT
            },
        },
        "macro": {"economy": {"souls_per_min": {"value": 500.0, "unit": "per_min"}}},
    }
    distributions = {"accuracy": ratio_dist, "net_worth_per_min": DIST}

    attach(features, distributions)

    assert features["micro"]["aim"]["accuracy"]["benchmark_percentile"] == 50.0
    assert "benchmark_percentile" not in features["micro"]["aim"]["crit_rate"]
    assert "benchmark_percentile" not in features["micro"]["abilities"]["ability_kill_share"]
    assert features["macro"]["economy"]["souls_per_min"]["benchmark_percentile"] == 50.0


def test_attach_ignores_stats_missing_from_the_distribution_response():
    features = {"micro": {"aim": {"accuracy": {"value": 0.5, "unit": "ratio"}}}}
    attach(features, {})  # API returned nothing for "accuracy"
    assert "benchmark_percentile" not in features["micro"]["aim"]["accuracy"]


@respx.mock
def test_fetch_hero_distributions_skips_badge_filter_when_unranked():
    route = respx.get(f"{BASE_URL}/v1/analytics/player-stats/metrics").mock(
        return_value=httpx.Response(200, json={"accuracy": DIST})
    )
    client = DeadlockClient(min_interval_s=0.0)
    fetch_hero_distributions(client, hero_id=17, average_badge=0)
    params = route.calls.last.request.url.params
    assert params["hero_ids"] == "17"
    assert "min_average_badge" not in params
    assert "max_average_badge" not in params
    client.close()


@respx.mock
def test_fetch_hero_distributions_windows_around_our_badge():
    route = respx.get(f"{BASE_URL}/v1/analytics/player-stats/metrics").mock(
        return_value=httpx.Response(200, json={})
    )
    client = DeadlockClient(min_interval_s=0.0)
    fetch_hero_distributions(client, hero_id=17, average_badge=60, badge_window=6)
    params = route.calls.last.request.url.params
    assert params["min_average_badge"] == "54"
    assert params["max_average_badge"] == "66"
    client.close()
