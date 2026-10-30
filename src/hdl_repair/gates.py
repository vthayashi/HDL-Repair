"""Verification gates: compile + functional-equivalence simulation.

Gate 1 (compile): iverilog compiles the design with the candidate file in place
of the golden target file (the golden tree is never mutated; the candidate path
is simply substituted into the file list).

Gate 2 (equivalence): run the golden testbench and require a functional pass.
The secworks tb_aes emits the legacy "*** All NN test cases completed
successfully" summary, which ``parse_tb_markers`` recognizes; explicit
TB_FUNCTIONAL_PASS/FAIL markers are also honored.

Also exposes ``classify_errors`` implementing the synthesis-failure taxonomy from
arXiv:2603.11287 (late syntax, undefined module, non-synthesizable construct,
simulation-only system task, invalid nesting) to steer the repair prompt.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def run_cmd(cmd: list[str], cwd: Path, timeout_sec: int = 300) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        return {"ok": proc.returncode == 0, "code": proc.returncode, "out": proc.stdout}
    except FileNotFoundError as e:
        return {"ok": False, "code": 127, "out": f"tool not found: {e}"}
    except subprocess.TimeoutExpired as e:
        return {"ok": False, "code": -1, "out": f"TIMEOUT: {e}"}


def parse_tb_markers(sim_log: str) -> dict[str, bool]:
    """Functional pass/fail from explicit markers or the legacy secworks summary."""
    up = (sim_log or "").upper()
    legacy_ok = ("*** ALL " in up) and ("TEST CASES COMPLETED SUCCESSFULLY" in up)
    legacy_fail = ("*** ERROR:" in up) or ("DID NOT COMPLETE SUCCESSFULLY" in up)

    functional_fail = ("TB_FUNCTIONAL_FAIL" in up) or legacy_fail
    functional_pass = (
        (("TB_FUNCTIONAL_PASS" in up) and ("TB_FUNCTIONAL_FAIL" not in up))
        or (legacy_ok and not legacy_fail)
    )
    return {"functional_pass": functional_pass, "functional_fail": functional_fail}


# --- synthesis/compile failure taxonomy (arXiv:2603.11287) ---------------------
_TAXONOMY = [
    ("undefined-module", [
        "unknown module type", "cannot find module", "unresolved module",
        "referenced in", "not a valid module", "module `",
    ]),
    ("non-synthesizable-construct", [
        "is not synthesizable", "unsupported", "not supported for synthesis",
        "found non-synthesizable", "delay", "initial block", "real ",
        "fork", "not synthesizable",
    ]),
    ("sim-only-system-task", [
        "$display", "$finish", "$monitor", "$dumpfile", "$dumpvars",
        "$time", "$random", "$stop",
    ]),
    ("invalid-nesting", [
        "variable declaration in unnamed block", "nested", "unexpected end",
        "premature end",
    ]),
    ("late-syntax-error", [
        "syntax error", "error: ", "malformed", "l-value", "unexpected",
    ]),
    ("synthesis-timeout", ["timeout"]),
]


def classify_errors(log_text: str) -> list[str]:
    """Return the taxonomy categories whose signatures appear in a tool log."""
    low = (log_text or "").lower()
    hits: list[str] = []
    for category, patterns in _TAXONOMY:
        if any(p in low for p in patterns) and category not in hits:
            hits.append(category)
    return hits


@dataclass
class GateResult:
    compile_ok: bool = False
    equiv_pass: bool = False      # functional equivalence to golden testbench
    sim_ran: bool = False
    compile_log: str = ""
    sim_log: str = ""
    error_categories: list[str] = field(default_factory=list)
    backend: str = "iverilog"

    @property
    def log_tail(self) -> str:
        combined = (self.compile_log + "\n" + self.sim_log).strip()
        return combined[-2000:]


def _simulate_iverilog(rtl_paths: list[Path], tb_path: Path, work_dir: Path,
                       timeout_sec: int) -> GateResult:
    res = GateResult(backend="iverilog")
    if not shutil.which("iverilog"):
        res.compile_log = "iverilog not found in PATH."
        res.error_categories = ["tooling-missing"]
        return res

    work_dir.mkdir(parents=True, exist_ok=True)
    sim_bin = work_dir / "equiv.sim"
    comp = run_cmd(
        ["iverilog", "-g2012", "-Wall", "-o", str(sim_bin), str(tb_path)]
        + [str(p) for p in rtl_paths],
        work_dir,
        timeout_sec=timeout_sec,
    )
    res.compile_log = comp.get("out", "")
    res.compile_ok = comp.get("ok", False)
    if not res.compile_ok:
        res.error_categories = classify_errors(res.compile_log)
        return res

    # run
    if shutil.which("vvp"):
        run = run_cmd(["vvp", str(sim_bin)], work_dir, timeout_sec=timeout_sec)
    else:
        exe = sim_bin
        exe_win = Path(str(sim_bin) + ".exe")
        if os.name == "nt" and not exe.exists() and exe_win.exists():
            exe = exe_win
        run = run_cmd([str(exe)], work_dir, timeout_sec=timeout_sec)

    res.sim_ran = True
    res.sim_log = run.get("out", "")
    markers = parse_tb_markers(res.sim_log)
    res.equiv_pass = bool(run.get("ok") and markers["functional_pass"])
    if not res.equiv_pass:
        res.error_categories = classify_errors(res.compile_log + "\n" + res.sim_log)
    return res


def run_equivalence_gate(rtl_paths: list[Path], tb_path: Path, work_dir: Path,
                         timeout_sec: int = 600, backend: str = "auto") -> GateResult:
    """Compile + simulate the design against the golden testbench.

    ``backend`` is accepted for API compatibility; fusesoc is not required and
    iverilog is used directly (the golden tree is untouched).
    """
    return _simulate_iverilog(rtl_paths, tb_path, work_dir, timeout_sec)
