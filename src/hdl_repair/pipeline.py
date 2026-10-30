"""Repair pipeline — the PAIR-style loop, re-framed for synthesizability.

Flow per run:
  iter 1  : INGEST the input LLM HDL (no LLM call) and evaluate the gates.
  iter 2+ : ANALYZE the failing gate -> REPAIR with the LLM -> evaluate again.
  stop    : when compile + synthesize + equivalence all pass (ACCEPT), or when
            max_iterations / token budget is reached.

The golden tree is never mutated: each gate runs against the golden RTL files
with the candidate path substituted for the single target file.

Artifacts: runs/run_<ts>/iter_NN/<candidate>.v, repair_iterations.jsonl,
repair_iterations.md, repair_summary.json, and (on success) a repaired/ copy.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import analyst as analyst_mod
from . import repairer as repairer_mod
from .config import RUNS_DIR, DesignSpec, RepairConfig
from .gates import run_equivalence_gate
from .llm_client import LLMClient
from .rtl_io import module_name_of
from .scoring import compute_repair_score, rank_tuple
from .synthesis import read_baseline, run_synthesis_gate


def _now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@dataclass
class IterationRecord:
    iter_id: int
    timestamp: str
    phase: str                     # ingest | repair
    model_used: str
    total_tokens: int
    compile_ok: bool
    synthesizable: bool
    synth_available: bool
    sta_available: bool
    equiv_pass: bool
    area: Optional[float]
    cells: Optional[int]
    slack: Optional[float]
    area_overhead_pct: Optional[float]
    score: float
    decision: str                  # ACCEPT | CONTINUE
    issue_categories: list[str] = field(default_factory=list)
    candidate_path: str = ""
    candidate_hash: str = ""
    analyst_source: str = ""
    log_tail: str = ""


class RepairPipeline:
    _RUN_COUNTER = itertools.count(1)

    def __init__(self, cfg: RepairConfig, spec: DesignSpec, verbose: bool = True):
        self.cfg = cfg
        self.spec = spec
        self.verbose = verbose
        self.client = LLMClient(cfg)
        self.baseline = read_baseline(spec.baseline_area_report, spec.baseline_sta_report)

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[{dt.datetime.utcnow().strftime('%H:%M:%S')}] {msg}", flush=True)

    def _compose_rtl_paths(self, candidate_path: Path) -> list[Path]:
        """Golden RTL file list with the target file replaced by the candidate."""
        paths: list[Path] = []
        for name in self.spec.rtl_files:
            if name == self.spec.target_rtl:
                paths.append(candidate_path)
            else:
                paths.append(self.spec.rtl_dir / name)
        return paths

    def _evaluate(self, candidate_path: Path, iter_dir: Path) -> dict[str, Any]:
        rtl_paths = self._compose_rtl_paths(candidate_path)

        # 1. SIMULATION EVALUATION
        sim_dir = iter_dir / "sim"
        sim_dir.mkdir(parents=True, exist_ok=True)
        gate = run_equivalence_gate(
            rtl_paths, self.spec.tb_file, sim_dir,
            timeout_sec=self.cfg.sim_timeout_sec, backend=self.cfg.sim_backend,
        )

        # Always force-write simulation logs to disk
        compile_log = getattr(gate, "compile_log", "") or "No compilation log generated."
        sim_log = getattr(gate, "sim_log", "") or "No simulation log generated."
        (sim_dir / "compile.log").write_text(compile_log, encoding="utf-8")
        (sim_dir / "sim.log").write_text(sim_log, encoding="utf-8")

        if not gate.compile_ok:
            print(f"\n[EVAL ERROR] ❌ Compilation Failed for {candidate_path.name}! Check: {sim_dir / 'compile.log'}")
        elif not gate.equiv_pass:
            print(f"\n[EVAL ERROR] ❌ Equivalence / Testbench Failed for {candidate_path.name}! Check: {sim_dir / 'sim.log'}")

        # 2. SYNTHESIS EVALUATION
        synth = None
        synth_dir = iter_dir / "synth"
        synth_dir.mkdir(parents=True, exist_ok=True)

        if not self.cfg.run_synthesis:
            msg = "run_synthesis is set to FALSE in RepairConfig!"
            print(f"\n[EVAL WARNING] ⚠️ SYNTHESIS SKIPPED: {msg}")
            (synth_dir / "SYNTH_SKIPPED.txt").write_text(msg, encoding="utf-8")
        else:
            synth = run_synthesis_gate(
                rtl_paths, self.spec.top_module, self.spec.liberty,
                synth_dir, self.baseline, timeout_sec=self.cfg.synth_timeout_sec,
            )

            # Always force-write synthesis and OpenSTA logs to disk
            yosys_log = getattr(synth, "log", "") or "No Yosys log captured."
            sta_log = getattr(synth, "sta_log", "") or "No OpenSTA log captured."
            (synth_dir / "yosys.log").write_text(yosys_log, encoding="utf-8")
            (synth_dir / "opensta.log").write_text(sta_log, encoding="utf-8")

            if not synth.available:
                print(f"\n[EVAL ERROR] ❌ Yosys/Liberty Unavailable: {synth.skipped_reason}")
                (synth_dir / "ERROR_UNAVAILABLE.txt").write_text(synth.skipped_reason, encoding="utf-8")
            elif not synth.synthesizable:
                print(f"\n[EVAL ERROR] ❌ Yosys Mapping Failed! Check: {synth_dir / 'yosys.log'}")
            elif not synth.sta_available:
                print(f"\n[EVAL WARNING] ⚠️ OpenSTA binary not found! Timing slack skipped.")

        return {"gate": gate, "synth": synth}

    def run(self, input_rtl: Path) -> dict[str, Any]:
        input_rtl = Path(input_rtl)
        candidate_text = input_rtl.read_text(encoding="utf-8", errors="ignore")
        module_name = module_name_of(candidate_text) or Path(self.spec.target_rtl).stem

        run_ts = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        run_id = next(self._RUN_COUNTER)

        run_dir = RUNS_DIR / f"run_{run_id:03d}_{self.spec.name}_{run_ts}"
        run_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = run_dir / "repair_iterations.jsonl"
        md_path = run_dir / "repair_iterations.md"

        self._log(f"run_dir={run_dir}")
        self._log(f"design={self.spec.name} target={self.spec.target_rtl} module={module_name}")
        self._log(f"baseline={self.baseline}")
        md_path.write_text(
            f"# Repair run {run_ts}\n\n"
            f"- design: {self.spec.name}\n"
            f"- target_rtl: {self.spec.target_rtl}\n"
            f"- input: {input_rtl}\n"
            f"- max_iterations: {self.cfg.max_iterations}\n"
            f"- baseline: area={self.baseline.get('area')}, cells={self.baseline.get('cells')}, slack={self.baseline.get('slack')}\n\n",
            encoding="utf-8",
        )

        total_tokens = 0
        best: Optional[IterationRecord] = None
        best_rank = (-1, -1, -1, -1.0)
        analyst_feedback: dict[str, Any] = {}
        accepted = False

        for i in range(1, self.cfg.max_iterations + 1):
            iter_dir = run_dir / f"iter_{i:02d}"
            iter_dir.mkdir(parents=True, exist_ok=True)
            phase = "ingest" if i == 1 else "repair"
            model_used = ""
            self._log(f"iteration {i}/{self.cfg.max_iterations} ({phase})")

            # --- obtain candidate -------------------------------------------------
            if i == 1:
                pass  # use the ingested input as-is
            else:
                rep = repairer_mod.repair(
                    self.client, self.cfg, self.spec, candidate_text,
                    analyst_feedback, analyst_feedback.get("_log_tail", ""), module_name,
                )
                model_used = rep.get("model", "")
                usage = rep.get("usage", {}) or {}
                total_tokens += int(usage.get("total_tokens", 0))
                if not rep.get("ok"):
                    self._log(f"iteration {i}: repair did not yield a valid module: {rep.get('errors')}")
                    if not self.client.available:
                        self._log("LLM unavailable; stopping repair loop after ingest evaluation.")
                        break
                else:
                    candidate_text = rep["rtl"]

            candidate_path = iter_dir / self.spec.target_rtl
            candidate_path.write_text(candidate_text, encoding="utf-8")

            # --- evaluate gates ---------------------------------------------------
            ev = self._evaluate(candidate_path, iter_dir)
            gate = ev["gate"]
            synth = ev["synth"]

            synth_available = bool(synth.available) if synth is not None else False
            sta_available = bool(synth.sta_available) if synth is not None else False
            synthesizable = bool(synth.synthesizable) if (synth and synth.available) else False
            area = synth.area if synth else None
            cells = synth.cells if synth else None
            slack = synth.slack if synth else None
            overhead = synth.area_overhead_pct if synth else None

            # Issue categories come from whichever gate failed.
            categories: list[str] = []
            log_parts = [gate.compile_log, gate.sim_log]
            categories += gate.error_categories
            if synth is not None and synth.available and not synthesizable:
                categories += synth.error_categories
                log_parts.append(synth.log)
            categories = list(dict.fromkeys(categories))  # dedupe, keep order
            log_tail = ("\n".join(p for p in log_parts if p)).strip()[-2500:]

            # ACCEPT requires compile + equivalence + (synthesizable OR synth not available).
            synth_ok_for_accept = synthesizable or (synth is None) or (not synth_available)
            all_pass = gate.compile_ok and gate.equiv_pass and synth_ok_for_accept
            score = compute_repair_score(gate.compile_ok, synthesizable, gate.equiv_pass, overhead)
            decision = "ACCEPT" if all_pass else "CONTINUE"

            self._log(
                f"iteration {i}: compile_ok={gate.compile_ok} synthesizable={synthesizable} "
                f"(avail={synth_available}, sta_avail={sta_available}) equiv_pass={gate.equiv_pass} score={score} -> {decision}"
            )

            rec = IterationRecord(
                iter_id=i, timestamp=_now_iso(), phase=phase, model_used=model_used,
                total_tokens=total_tokens, compile_ok=gate.compile_ok,
                synthesizable=synthesizable, synth_available=synth_available,
                sta_available=sta_available, equiv_pass=gate.equiv_pass,
                area=area, cells=cells, slack=slack, area_overhead_pct=overhead,
                score=score, decision=decision, issue_categories=categories,
                candidate_path=str(candidate_path), candidate_hash=_sha(candidate_text),
                log_tail=log_tail,
            )

            # --- analyst (only if continuing) ------------------------------------
            if decision != "ACCEPT":
                fb = analyst_mod.analyze(
                    self.client, self.cfg, candidate_text, log_tail, categories,
                    gate.compile_ok, synthesizable, gate.equiv_pass,
                )
                fb["_log_tail"] = log_tail
                analyst_feedback = fb
                rec.analyst_source = fb.get("source", "")
                usage = fb.get("usage", {}) or {}
                total_tokens += int(usage.get("total_tokens", 0))
                rec.total_tokens = total_tokens

            # --- persist ----------------------------------------------------------
            rec_dict = asdict(rec)
            rec_dict["analyst_feedback"] = {k: v for k, v in analyst_feedback.items() if not k.startswith("_")}
            with jsonl_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec_dict, ensure_ascii=True) + "\n")
            with md_path.open("a", encoding="utf-8") as f:
                f.write(
                    f"\n## Iteration {i} ({phase})\n"
                    f"- compile_ok: {rec.compile_ok}\n"
                    f"- synthesizable: {rec.synthesizable} (available: {rec.synth_available})\n"
                    f"- equiv_pass: {rec.equiv_pass}\n"
                    f"- Yosys Metrics: area={rec.area}, cells={rec.cells}, area_overhead_pct={rec.area_overhead_pct}%\n"
                    f"- OpenSTA Metrics: slack={rec.slack} (sta_available: {rec.sta_available})\n"
                    f"- score: {rec.score}\n"
                    f"- decision: {rec.decision}\n"
                    f"- issues: {rec.issue_categories}\n"
                    f"- candidate: {rec.candidate_path}\n"
                )

            cur_rank = rank_tuple(gate.compile_ok, synthesizable, gate.equiv_pass, score)
            if cur_rank > best_rank:
                best, best_rank = rec, cur_rank

            if total_tokens >= self.cfg.max_total_tokens_est:
                self._log(f"stopping: token budget reached ({total_tokens})")
                break
            if decision == "ACCEPT":
                accepted = True
                self._log(f"iteration {i}: ACCEPT")
                break

        # --- materialize best candidate ------------------------------------------
        repaired_dir = run_dir / "repaired"
        repaired_dir.mkdir(exist_ok=True)
        if best is not None:
            shutil.copy2(best.candidate_path, repaired_dir / self.spec.target_rtl)

        summary = {
            "run_dir": str(run_dir),
            "design": self.spec.name,
            "target_rtl": self.spec.target_rtl,
            "input": str(input_rtl),
            "accepted": accepted,
            "synthesis_verified": bool(best and best.synth_available),
            "sta_verified": bool(best and best.sta_available),
            "best_iteration": asdict(best) if best else None,
            "baseline": self.baseline,
            "total_tokens_est": total_tokens,
            "jsonl_log": str(jsonl_path),
            "md_log": str(md_path),
            "repaired_rtl": str(repaired_dir / self.spec.target_rtl) if best else None,
        }
        (run_dir / "repair_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        self._log(f"run finished: accepted={accepted} synthesis_verified={summary['synthesis_verified']} sta_verified={summary['sta_verified']}")
        return summary