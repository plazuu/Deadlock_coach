"""Macro / wave & lane management.

Not derivable from `match_info` — freeze/push state needs the creep-wave
timeline, which only the Phase 6 demo query exposes. Stubbed honestly rather
than approximated from weak proxies.
"""

from __future__ import annotations

from ..common import Leaf

SUB = "wave_management"


def extract() -> list[Leaf]:
    return [
        Leaf(
            "wave_management",
            "macro",
            SUB,
            None,
            "",
            needs_demo=True,
            note="requires the Phase 6 demo-query (creep wave state over time)",
        )
    ]
