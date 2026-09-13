from __future__ import annotations

import pytest

from limpet.features.common import (
    euclidean,
    final_stats,
    per_min,
    phase_of,
    safe_ratio,
    stats_at_or_before,
)


@pytest.mark.parametrize(
    ("t", "expected"),
    [(0, "lane"), (599, "lane"), (600, "mid"), (1199, "mid"), (1200, "late")],
)
def test_phase_of(t, expected):
    assert phase_of(t) == expected


def test_safe_ratio():
    assert safe_ratio(3, 4) == 0.75
    assert safe_ratio(0, 4) == 0.0
    assert safe_ratio(3, 0) is None
    assert safe_ratio(None, 4) is None


def test_per_min():
    assert per_min(120, 60) == 120.0
    assert per_min(120, 0) is None
    assert per_min(None, 60) is None


def test_euclidean():
    a = {"x": 0, "y": 0, "z": 0}
    b = {"x": 3, "y": 4, "z": 0}
    assert euclidean(a, b) == 5.0
    assert euclidean(None, b) is None


def test_stats_at_or_before():
    stats = [{"time_stamp_s": 100, "v": "a"}, {"time_stamp_s": 300, "v": "b"}]
    assert stats_at_or_before(stats, 50)["v"] == "a"  # before first sample -> earliest
    assert stats_at_or_before(stats, 150)["v"] == "a"
    assert stats_at_or_before(stats, 300)["v"] == "b"
    assert stats_at_or_before(stats, 999)["v"] == "b"
    assert stats_at_or_before([], 100) is None


def test_final_stats():
    assert final_stats([{"a": 1}, {"a": 2}])["a"] == 2
    assert final_stats([]) is None
    assert final_stats(None) is None
