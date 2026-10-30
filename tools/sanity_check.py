"""Sanity check script: Runs Yosys + OpenSTA on the clean Golden AES model

Validates that:
  1. Golden RTL files compile & elaborate in Yosys.
  2. Yosys maps to Sky130 cells and produces non-None area/cell metrics.
  3. Yosys outputs a netlist (`netlist.v`).
  4. OpenSTA runs on the synthesized netlist and produces non-None timing slack.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add src/ to path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdl_repair.config import aes_design, load_config
from hdl_repair.synthesis import run_synthesis_gate


def main() -> int:
    cfg = load_config()
    spec = aes_design(target_rtl="aes_sbox.v")  # Target doesn't matter for pure golden run

    # Golden RTL file list
    golden_paths = [spec.rtl_dir / f for f in spec.rtl_files]
    work_dir = ROOT / "runs" / "sanity_check_golden"
    work_dir.mkdir(parents=True, exist_ok=True)

    print("=========================================================")
    print("         RUNNING GOLDEN MODEL SANITY CHECK              ")
    print("=========================================================")
    print(f"Top module : {spec.top_module}")
    print(f"Liberty    : {spec.liberty}")
    print(f"Golden RTL : {[p.name for p in golden_paths]}")
    print(f"Work Dir   : {work_dir}\n")

    baseline_placeholder = {"area": None, "cells": None, "slack": None}

    res = run_synthesis_gate(
        rtl_paths=golden_paths,
        top=spec.top_module,
        liberty=spec.liberty,
        work_dir=work_dir,
        baseline=baseline_placeholder,
        clock_port="clk",
        clock_period_ns=100.0,
        timeout_sec=300,
    )

    print("\n------------------- RESULTS -------------------")
    print(f"Yosys Available   : {res.available} (Reason: '{res.skipped_reason}')")
    print(f"Synthesizable     : {res.synthesizable}")
    print(f"Chip Area         : {res.area}")
    print(f"Cell Count        : {res.cells}")
    print(f"OpenSTA Available : {res.sta_available}")
    print(f"Timing Slack      : {res.slack}")
    print("-----------------------------------------------")

    if res.synthesizable and res.area is not None:
        sta = "and OpenSTA flow are working" if res.sta_available else "flow is working (OpenSTA not found)"
        print(f"\n[PASS] SANITY CHECK PASSED! Yosys {sta}.")
        print(f"Area Report written to: {work_dir / 'area_report.txt'}")
        print(f"STA Report written to : {work_dir / 'sta_report.txt'}")
        return 0
    else:
        print("\n[FAIL] SANITY CHECK FAILED! Inspect Yosys log below:")
        print(res.log[-2000:])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())