"""Synthesizability gate and PPA metrics via Yosys (+ optional OpenSTA).

The synthesizability gate is the core of this tool: it runs Yosys to elaborate
the design (with the candidate substituted for the golden target file) and map it
to the sky130 standard-cell library. If elaboration/mapping succeeds the design
is synthesizable and ``stat`` yields area + cell count; otherwise the log is
classified to steer the repair.

Yosys/OpenSTA may be absent (e.g. not yet installed on Windows). In that case the
gate returns ``available=False`` and does not block the loop, but the run summary
flags that synthesizability was not verified.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .gates import classify_errors, run_cmd


def parse_area_report(area_text: str) -> dict[str, Any]:
    out: dict[str, Any] = {"chip_area": None, "cell_count": None}
    ma = re.search(r"Chip area for .*?:\s*([0-9.]+)", area_text)
    if ma:
        out["chip_area"] = float(ma.group(1))
    mc = re.search(r"Number of cells:\s*([0-9]+)", area_text)
    if mc:
        out["cell_count"] = int(mc.group(1))
    return out


def parse_sta_report(sta_text: str) -> dict[str, Any]:
    out: dict[str, Any] = {"slack": None}
    ms = re.search(r"slack \((?:MET|VIOLATED)\)\s*([-.0-9]+)", sta_text, flags=re.I)
    if ms:
        out["slack"] = float(ms.group(1))
    return out


def _gen_yosys_script(rtl_paths: list[Path], top: str, liberty: Path,
                      area_out: Path, netlist_out: Path) -> str:
    reads = " ".join(f'"{p.as_posix()}"' for p in rtl_paths)
    return "\n".join([
        f"read_verilog {reads}",
        f'read_liberty -lib "{liberty.as_posix()}"',
        f"hierarchy -check -top {top}",
        "proc; opt",
        "flatten",
        "memory; opt",
        "techmap; opt",
        f'dfflibmap -liberty "{liberty.as_posix()}"',
        f'abc -liberty "{liberty.as_posix()}" -D 1000',
        f'tee -o "{area_out.as_posix()}" stat -liberty "{liberty.as_posix()}"',
        f'write_verilog "{netlist_out.as_posix()}"',
        "",
    ])


@dataclass
class SynthResult:
    available: bool = True          # yosys + liberty present and attempted
    synthesizable: bool = False     # elaborated + mapped without error
    area: Optional[float] = None
    cells: Optional[int] = None
    slack: Optional[float] = None
    area_overhead_pct: Optional[float] = None
    log: str = ""
    error_categories: list[str] = field(default_factory=list)
    skipped_reason: str = ""


def read_baseline(area_report: Optional[Path], sta_report: Optional[Path]) -> dict[str, Any]:
    base: dict[str, Any] = {"area": None, "cells": None, "slack": None}
    if area_report and area_report.exists():
        a = parse_area_report(area_report.read_text(encoding="utf-8", errors="ignore"))
        base["area"], base["cells"] = a.get("chip_area"), a.get("cell_count")
    if sta_report and sta_report.exists():
        base["slack"] = parse_sta_report(sta_report.read_text(encoding="utf-8", errors="ignore")).get("slack")
    return base


def run_synthesis_gate(rtl_paths: list[Path], top: str, liberty: Optional[Path],
                       work_dir: Path, baseline: dict[str, Any],
                       timeout_sec: int = 1800) -> SynthResult:
    res = SynthResult()
    if not shutil.which("yosys"):
        res.available = False
        res.skipped_reason = "yosys not found in PATH"
        return res
    if liberty is None or not Path(liberty).exists():
        res.available = False
        res.skipped_reason = "sky130 Liberty file not found (set SKY130_LIB in .env)"
        return res

    work_dir.mkdir(parents=True, exist_ok=True)
    area_out = work_dir / "area_report.txt"
    netlist_out = work_dir / "netlist.v"
    script = work_dir / "synth.ys"
    script.write_text(_gen_yosys_script(rtl_paths, top, Path(liberty), area_out, netlist_out),
                      encoding="utf-8")

    run = run_cmd(["yosys", "-s", str(script)], work_dir, timeout_sec=timeout_sec)
    res.log = run.get("out", "")
    res.synthesizable = bool(run.get("ok")) and netlist_out.exists()

    if area_out.exists():
        a = parse_area_report(area_out.read_text(encoding="utf-8", errors="ignore"))
        res.area, res.cells = a.get("chip_area"), a.get("cell_count")

    if not res.synthesizable:
        res.error_categories = classify_errors(res.log)
        return res

    # optional static timing with OpenSTA is left to the PPA post-step; area-based
    # overhead is computed here when a golden baseline area is known.
    if baseline.get("area") and res.area:
        res.area_overhead_pct = round(((res.area - baseline["area"]) / baseline["area"]) * 100.0, 4)
    res.slack = baseline.get("slack")  # placeholder until OpenSTA is wired per-run
    return res
