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
    # older Yosys: "Number of cells: N"; newer: "    N <area> cells" (first = top module)
    mc = (re.search(r"Number of cells:\s*([0-9]+)", area_text)
          or re.search(r"^\s*([0-9]+)\s+\S+\s+cells\s*$", area_text, re.MULTILINE))
    if mc:
        out["cell_count"] = int(mc.group(1))
    return out


# Yosys internal cells that legitimately survive mapping (hierarchy metadata only).
_BENIGN_INTERNAL_CELLS = {"$scopeinfo"}


def unmapped_cells(area_text: str) -> list[str]:
    """Yosys internal ``$`` cells left in the mapped netlist, e.g. ``$print`` from a
    ``$display`` in the DUT. Any of them means no standard-cell implementation."""
    # new stat format: "    1    -   $print"; old: "     $print     1"
    found = re.findall(r"^\s*(?:\d+\s+\S+\s+)?(\$\w+)(?:\s+\d+)?\s*$", area_text or "", re.MULTILINE)
    return sorted(set(found) - _BENIGN_INTERNAL_CELLS)


def parse_sta_report(sta_text: str) -> dict[str, Any]:
    """Parses OpenSTA output (file content or stdout) to extract slack."""
    out: dict[str, Any] = {"slack": None}
    if not sta_text:
        return out

    # 1. Match OpenSTA endpoint lines: "... 99.1807 20.5157 78.6649 (MET)".
    # One per path group (e.g. core_clock, asynchronous); report the worst.
    slacks = re.findall(r"([-.0-9]+)\s*\((?:MET|VIOLATED)\)", sta_text, flags=re.I)
    if slacks:
        out["slack"] = min(float(s) for s in slacks)
        return out

    # 2. Match summary line: "slack (MET) 78.6649" or "slack -1.25"
    m_summary = re.search(r"slack\s*(?:\((?:MET|VIOLATED)\))?\s*([-.0-9]+)", sta_text, flags=re.I)
    if m_summary:
        out["slack"] = float(m_summary.group(1))

    return out

def _gen_yosys_script(
    rtl_paths: list[Path],
    top: str,
    liberty: Path,
    area_out: Path,
    netlist_out: Path,
) -> str:
    abs_reads = " ".join(f'"{Path(p).resolve().as_posix()}"' for p in rtl_paths)
    abs_lib = Path(liberty).resolve().as_posix()
    abs_area = Path(area_out).resolve().as_posix()
    abs_netlist = Path(netlist_out).resolve().as_posix()

    return "\n".join([
        f"read_verilog {abs_reads}",
        f'read_liberty -lib "{abs_lib}"',
        f"hierarchy -check -top {top}",
        "proc; opt",
        "flatten; opt",
        "memory; opt",
        "techmap; opt",
        f'dfflibmap -liberty "{abs_lib}"',
        f'abc -liberty "{abs_lib}" -D 1000',
        f'tee -o {abs_area} stat -liberty "{abs_lib}"',      # <-- No quotes around {abs_area}
        f'write_verilog -noattr {abs_netlist}',             # <-- No quotes around {abs_netlist}
        "",
    ])


def _gen_opensta_script(
    netlist_path: Path,
    top: str,
    liberty: Path,
    sta_out: Path,
    clock_port: str = "clk",
    clock_period_ns: float = 100.0,
) -> str:
    abs_lib = Path(liberty).resolve().as_posix()
    abs_netlist = Path(netlist_path).resolve().as_posix()
    abs_sta_out = Path(sta_out).resolve().as_posix()

    return "\n".join([
        "set_cmd_units -time ns -capacitance pF -current mA -voltage V -resistance kOhm -distance um",
        f'read_liberty "{abs_lib}"',
        f'read_verilog "{abs_netlist}"',
        f"link_design {top}",
        f"create_clock -name core_clock -period {clock_period_ns} [get_ports {clock_port}]",
        "set_input_delay 0.0 -clock core_clock [all_inputs -no_clocks]",
        "set_output_delay 0.0 -clock core_clock [all_outputs]",
        f'report_checks -path_delay max -format end -digits 4 > "{abs_sta_out}"',
        "",
    ])

@dataclass
class SynthResult:
    available: bool = True          # yosys + liberty present and attempted
    sta_available: bool = True      # sta binary present and attempted
    synthesizable: bool = False     # elaborated + mapped without error
    area: Optional[float] = None
    cells: Optional[int] = None
    slack: Optional[float] = None
    area_overhead_pct: Optional[float] = None
    log: str = ""                   # Yosys output log
    sta_log: str = ""               # OpenSTA output log
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

def run_synthesis_gate(
    rtl_paths: list[Path],
    top: str,
    liberty: Optional[Path],
    work_dir: Path,
    baseline: dict[str, Any],
    clock_port: str = "clk",
    clock_period_ns: float = 100.0,
    timeout_sec: int = 1800,
) -> SynthResult:
    res = SynthResult()

    # 1. Check Yosys & Liberty
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

    # 2. Run Yosys
    script = work_dir / "synth.ys"
    script.write_text(
        _gen_yosys_script(rtl_paths, top, Path(liberty), area_out, netlist_out),
        encoding="utf-8",
    )

    run = run_cmd(["yosys", "-s", str(script)], work_dir, timeout_sec=timeout_sec)
    res.log = run.get("out", "")
    res.synthesizable = bool(run.get("ok")) and netlist_out.exists()

    leftover: list[str] = []
    if area_out.exists():
        area_text = area_out.read_text(encoding="utf-8", errors="ignore")
        a = parse_area_report(area_text)
        res.area, res.cells = a.get("chip_area"), a.get("cell_count")
        leftover = unmapped_cells(area_text)

    if res.synthesizable and leftover:
        # Yosys "succeeds" but keeps behavioral cells it cannot map to the library
        res.synthesizable = False
        res.log += (
            f"\nERROR: unmapped cells after technology mapping: {', '.join(leftover)}. "
            "These have no standard-cell implementation (e.g. $print comes from "
            "$display/$write/$monitor inside the design).\n"
        )
        res.error_categories = (["sim-only-system-task"] if "$print" in leftover else []) + \
            (["non-synthesizable-construct"] if set(leftover) - {"$print"} else [])
        return res

    if not res.synthesizable:
        res.error_categories = classify_errors(res.log)
        return res

    if baseline.get("area") and res.area:
        res.area_overhead_pct = round(
            ((res.area - baseline["area"]) / baseline["area"]) * 100.0, 4
        )

    # 3. Run OpenSTA (if binary present)
    sta_bin = shutil.which("sta") or shutil.which("opensta")
    if not sta_bin:
        res.sta_available = False
        res.slack = baseline.get("slack")
        return res

    sta_script = work_dir / "run_sta.tcl"
    sta_out = work_dir / "sta_report.txt"

    sta_script.write_text(
        _gen_opensta_script(
            netlist_out, top, Path(liberty), sta_out, clock_port, clock_period_ns
        ),
        encoding="utf-8",
    )

    sta_run = run_cmd([sta_bin, str(sta_script)], work_dir, timeout_sec=timeout_sec)
    res.sta_log = sta_run.get("out", "")

    if sta_out.exists():
        sta_parsed = parse_sta_report(
            sta_out.read_text(encoding="utf-8", errors="ignore")
        )
        res.slack = sta_parsed.get("slack")

    return res
