"""Shared helpers for feature leaves: the `Leaf` record, phase windows, and the
small numeric helpers every `micro/*` and `macro/*` module builds on.

Every observation the coach can make is a `Leaf`, tagged `micro` or `macro` per
`docs/PLAN.md` §1. Feature modules return `list[Leaf]`; `features/extract.py`
buckets them into the `{micro: {...}, macro: {...}}` shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Dimension = Literal["micro", "macro"]

# Coarse game-phase windows used to slice time-sampled stats.
LANE_PHASE_END_S = 10 * 60
MID_PHASE_END_S = 20 * 60


def phase_of(t: int) -> Literal["lane", "mid", "late"]:
    if t < LANE_PHASE_END_S:
        return "lane"
    if t < MID_PHASE_END_S:
        return "mid"
    return "late"


@dataclass(frozen=True)
class Leaf:
    """One coaching-relevant observation.

    `key` + `sub_dimension` + `dimension` together address the leaf in the
    nested `MatchFeatures` output; only `value`/`unit`/`needs_demo`/`note` are
    actually serialised per-leaf, since the nesting already encodes the rest.
    """

    key: str
    dimension: Dimension
    sub_dimension: str
    value: float | int | None
    unit: str = ""
    needs_demo: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"value": self.value, "unit": self.unit}
        if self.needs_demo:
            out["needs_demo"] = True
        if self.note:
            out["note"] = self.note
        return out


def final_stats(stats: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The last time-sampled `stats[]` entry — cumulative totals for the match."""
    return stats[-1] if stats else None


def stats_at_or_before(stats: list[dict[str, Any]] | None, t: int) -> dict[str, Any] | None:
    """Latest sample with `time_stamp_s <= t`, falling back to the earliest sample."""
    if not stats:
        return None
    candidates = [s for s in stats if s.get("time_stamp_s", 0) <= t]
    return candidates[-1] if candidates else stats[0]


def safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if not numerator and numerator != 0:
        return None
    if not denominator:
        return None
    return numerator / denominator


def per_min(value: float | None, duration_s: int) -> float | None:
    if value is None or duration_s <= 0:
        return None
    return value / (duration_s / 60)


def euclidean(a: dict[str, float] | None, b: dict[str, float] | None) -> float | None:
    if not a or not b:
        return None
    return (
        (a.get("x", 0) - b.get("x", 0)) ** 2
        + (a.get("y", 0) - b.get("y", 0)) ** 2
        + (a.get("z", 0) - b.get("z", 0)) ** 2
    ) ** 0.5
