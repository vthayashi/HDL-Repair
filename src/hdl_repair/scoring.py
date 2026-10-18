"""Repair quality score — re-framed from the AHA composite score.

No CVSS/stealth. The score rewards passing the three gates and penalizes area
overhead vs the golden baseline, giving a single comparable number per iteration
for ranking the best candidate and for the dissertation's quantitative tables.

    score = 40*compile_ok + 30*synth_ok + 30*equiv_ok - overhead_penalty
    overhead_penalty = clamp(area_overhead_pct, 0, 30)   # only when synthesizable
"""
from __future__ import annotations

from typing import Optional


def compute_repair_score(compile_ok: bool, synth_ok: bool, equiv_ok: bool,
                         area_overhead_pct: Optional[float]) -> float:
    score = 40.0 * bool(compile_ok) + 30.0 * bool(synth_ok) + 30.0 * bool(equiv_ok)
    if synth_ok and area_overhead_pct is not None and area_overhead_pct > 0:
        score -= min(30.0, float(area_overhead_pct))
    return round(max(0.0, score), 3)


def rank_tuple(compile_ok: bool, synth_ok: bool, equiv_ok: bool, score: float) -> tuple:
    """Lexicographic rank for selecting the best iteration (higher is better)."""
    return (int(compile_ok), int(synth_ok), int(equiv_ok), float(score))
