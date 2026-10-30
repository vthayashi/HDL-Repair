"""Per-iteration table (timings, tokens, gates, analyst) from a dataset run.

Reads ``results.csv`` (written by ``run_dataset.py``; needs its ``run_dir``
column) and each run's ``repair_iterations.jsonl``, and writes one row per
iteration. Step timings come from the ``timings`` field recorded by the
pipeline; for runs made before that field existed they are reconstructed from
the timestamps of the files each step writes:

    iter_NN/ created -> candidate .v (repair LLM) -> sim/equiv.sim (compile)
    -> sim/sim.log (simulation) -> synth/run_sta.tcl (Yosys)
    -> synth/opensta.log (OpenSTA) -> next iter_NN/ or repair_summary.json
    (analyst LLM)

Usage:
    python tools/summarize_runs.py --results datasets/results.csv \\
        --out datasets/iterations.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
STEPS = ["repair_llm", "compile", "sim", "yosys", "sta", "analyst_llm"]


def _born(p: Path) -> Optional[float]:
    if not p.exists():
        return None
    st = p.stat()
    return getattr(st, "st_birthtime", st.st_ctime if os.name == "nt" else st.st_mtime)


def _mtime(p: Path) -> Optional[float]:
    return p.stat().st_mtime if p.exists() else None


def _span(a: Optional[float], b: Optional[float]) -> float:
    return round(b - a, 3) if a is not None and b is not None and b >= a else 0.0


def timings_from_files(it_dir: Path, target: str, next_start: Optional[float]) -> dict[str, float]:
    """Reconstruct step durations from file timestamps of one iteration dir."""
    sim, syn = it_dir / "sim", it_dir / "synth"
    start = _born(it_dir)
    cand = _born(it_dir / target)
    exe = _mtime(sim / "equiv.sim")
    sim_log = _born(sim / "sim.log")
    compile_end = exe if exe is not None else _born(sim / "compile.log")
    yosys_start = _born(syn / "synth.ys")
    sta_script = _born(syn / "run_sta.tcl")
    yosys_end = sta_script if sta_script is not None else _born(syn / "yosys.log")
    gates_end = _born(syn / "opensta.log") or _born(syn / "yosys.log") or sim_log
    return {
        "repair_llm": _span(start, cand) if it_dir.name != "iter_01" else 0.0,
        "compile": _span(_born(sim), compile_end),
        "sim": _span(exe, sim_log) if exe is not None else 0.0,
        "yosys": _span(yosys_start, yosys_end),
        "sta": _span(sta_script, _born(syn / "opensta.log")) if sta_script else 0.0,
        "analyst_llm": _span(gates_end, next_start),
    }


def iteration_rows(row: dict[str, str], runs_root: Path) -> list[dict[str, Any]]:
    run_dir = runs_root / row["run_dir"]
    recs = [json.loads(l) for l in (run_dir / "repair_iterations.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    target = Path(json.loads((run_dir / "repair_summary.json").read_text(encoding="utf-8"))["target_rtl"]).name
    out, prev_tokens = [], 0
    for k, rec in enumerate(recs):
        it_dir = run_dir / f"iter_{rec['iter_id']:02d}"
        if rec.get("timings"):
            t = {s: float(rec["timings"].get(s, 0.0)) for s in STEPS}
            source = "recorded"
        else:
            nxt = _born(run_dir / f"iter_{recs[k + 1]['iter_id']:02d}") if k + 1 < len(recs) else None
            if nxt is None and rec["decision"] != "ACCEPT":
                nxt = _born(run_dir / "repair_summary.json")  # analyst ran after the last iteration
            t = timings_from_files(it_dir, target, nxt)
            source = "file-times"
        fb = rec.get("analyst_feedback") or {}
        analyst = "" if rec["decision"] == "ACCEPT" else rec.get("analyst_source", "")
        if analyst == "deterministic" and "llm_raw" in fb:
            analyst = "fallback (empty LLM reply)" if not fb["llm_raw"] else "fallback (unparsable LLM reply)"
        elif analyst == "deterministic" and fb.get("llm_error"):
            analyst = "fallback (LLM error)"
        out.append({
            "row": row["row"], "design": row["design"], "run_dir": row["run_dir"],
            "iter": rec["iter_id"], "phase": rec["phase"], "model": rec.get("model_used", ""),
            "compile_ok": rec["compile_ok"], "synthesizable": rec["synthesizable"],
            "equiv_pass": rec["equiv_pass"], "score": rec["score"], "decision": rec["decision"],
            "issue_categories": ",".join(rec.get("issue_categories") or []),
            "analyst": analyst,
            "tokens_step": rec["total_tokens"] - prev_tokens, "tokens_cum": rec["total_tokens"],
            "area": rec.get("area"), "cells": rec.get("cells"), "slack": rec.get("slack"),
            **{f"t_{s}": t[s] for s in STEPS},
            "t_total": round(sum(t.values()), 3), "timing_source": source,
        })
        prev_tokens = rec["total_tokens"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=Path, default=ROOT / "datasets" / "results.csv")
    ap.add_argument("--runs-root", type=Path, default=ROOT / "runs")
    ap.add_argument("--out", type=Path, default=ROOT / "datasets" / "iterations.csv")
    args = ap.parse_args()

    with args.results.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows or "run_dir" not in rows[0]:
        raise SystemExit(f"{args.results} has no run_dir column; re-run tools/run_dataset.py")

    table = [r for row in rows for r in iteration_rows(row, args.runs_root)]
    with args.out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(table[0]))
        w.writeheader()
        w.writerows(table)
    print(f"wrote {len(table)} iterations from {len(rows)} runs -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
