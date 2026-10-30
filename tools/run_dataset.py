"""Run the repair pipeline over a CSV of defective HDL examples and tabulate.

Reads a CSV with columns Design, Iteration, Attack_Prompt, Generated_Trojan,
LLM_Analysis (the schema of the professor's dataset and of the generated AES set),
writes each Generated_Trojan to a file, repairs it against the AES golden, and
prints a results table.

Usage:
    python tools/run_dataset.py --csv datasets/AES_defective_examples.csv
    python tools/run_dataset.py --csv ... --max-iter 1 --no-llm   # detection only
    python tools/run_dataset.py --csv ... --limit 4
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdl_repair.config import aes_design, load_config  # noqa: E402
from hdl_repair.pipeline import RepairPipeline  # noqa: E402

AES_FILES = {
    "aes.v", "aes_core.v", "aes_key_mem.v", "aes_encipher_block.v",
    "aes_decipher_block.v", "aes_sbox.v", "aes_inv_sbox.v",
}


def load_rows(csv_path: Path) -> list[dict]:
    raw = csv_path.read_text(encoding="utf-8", errors="replace")
    return list(csv.DictReader(io.StringIO(raw)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--max-iter", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="only run first N rows (0 = all)")
    ap.add_argument("--no-llm", action="store_true", help="disable repair (detection only)")
    ap.add_argument("--out", type=Path, default=ROOT / "datasets" / "results.csv")
    ap.add_argument("--codes-out", type=Path,
                    default=ROOT / "datasets" / "AES_repaired_examples.csv",
                    help="CSV with the repaired RTL of each example (for replication)")
    args = ap.parse_args()

    rows = load_rows(args.csv)
    if args.limit:
        rows = rows[: args.limit]

    gen_dir = ROOT / "runs" / "_dataset_inputs"
    gen_dir.mkdir(parents=True, exist_ok=True)

    results = []
    codes = []  # repaired RTL per example, for the replication CSV
    for i, row in enumerate(rows):
        design = (row.get("Design") or "").split("/")[-1].strip()
        if design not in AES_FILES:
            print(f"[{i}] skip: Design '{design}' is not an AES module")
            continue
        code = row.get("Generated_Trojan") or ""
        try:
            expected = json.loads(row.get("LLM_Analysis") or "{}")
        except Exception:
            expected = {}

        in_path = gen_dir / f"row{i:02d}_{design}"
        in_path.write_text(code, encoding="utf-8")

        cfg = load_config()
        cfg.max_iterations = args.max_iter
        cfg.run_synthesis = True  # Force Yosys & OpenSTA synthesis flow
        if args.no_llm:
            cfg.hf_token = None  # forces deterministic analyst + stop after ingest
        spec = aes_design(target_rtl=design)

        try:
            # Enable verbose=True to print execution alerts and progress to stdout
            t0 = time.perf_counter()
            summary = RepairPipeline(cfg, spec, verbose=True).run(in_path)
            wall_sec = round(time.perf_counter() - t0, 1)
            best = summary.get("best_iteration") or {}
            res = {
                "row": i,
                "design": design,
                "expected_category": expected.get("expected_category", ""),
                "detectable_now": expected.get("detectable_without_yosys", ""),
                "ingest_compile_ok": None,
                "final_compile_ok": best.get("compile_ok"),
                "final_equiv_pass": best.get("equiv_pass"),
                "synth_verified": summary.get("synthesis_verified"),
                "sta_verified": summary.get("sta_verified"),
                "area": best.get("area"),
                "cells": best.get("cells"),
                "slack": best.get("slack"),
                "accepted": summary.get("accepted"),
                "best_iter": best.get("iter_id"),
                "detected_categories": ",".join(best.get("issue_categories", []) or []),
                "tokens": summary.get("total_tokens_est"),
                "wall_sec": wall_sec,
                "run_dir": Path(summary["run_dir"]).name,
            }
            # recover iter-1 (ingest) detection from the jsonl
            try:
                jl = Path(summary["jsonl_log"])
                first = json.loads(jl.read_text(encoding="utf-8").splitlines()[0])
                res["ingest_compile_ok"] = first.get("compile_ok")
                res["ingest_equiv_pass"] = first.get("equiv_pass")
                res["ingest_synthesizable"] = first.get("synthesizable")
                res["ingest_categories"] = ",".join(first.get("issue_categories", []) or [])
            except Exception:
                pass
            results.append(res)

            # derive a human-readable status from what the gates observed
            gates = "compiles + synthesizable + equivalent" if res["synth_verified"] else "compiles + equivalent"
            if res["accepted"] and res["best_iter"] == 1:
                status = f"undetected (input passes all gates: {gates})"
            elif res["accepted"]:
                status = f"repaired ({gates})"
            elif res["final_compile_ok"]:
                status = "compiles, but not equivalent"
            else:
                status = "not repaired"
            repaired_code = code  # fall back to the input if nothing better materialized
            rp = summary.get("repaired_rtl")
            if rp and Path(rp).exists():
                repaired_code = Path(rp).read_text(encoding="utf-8", errors="ignore")
            codes.append({
                "Design": row.get("Design", design),
                "Defect_Category": expected.get("expected_category", ""),
                "Defect_Id": expected.get("defect_id", ""),
                "Detectable_Without_Yosys": res["detectable_now"],
                "Status": status,
                "Accepted": res["accepted"],
                "Final_Compile_OK": res["final_compile_ok"],
                "Final_Equiv_Pass": res["final_equiv_pass"],
                "Area": res["area"],
                "Cells": res["cells"],
                "Slack": res["slack"],
                "Iterations": res["best_iter"],
                "Tokens": res["tokens"],
                "Defective_Code": code,
                "Repaired_Code": repaired_code,
            })

            print(f"[{i}] {design:24} {expected.get('expected_category',''):28} "
                  f"accepted={res['accepted']} best_iter={res['best_iter']} "
                  f"tokens={res['tokens']}")
        except Exception as e:
            results.append({"row": i, "design": design,
                            "expected_category": expected.get("expected_category", ""),
                            "error": str(e)[:200]})
            print(f"[{i}] {design:24} ERROR: {str(e)[:120]}")

    # save results
    if results:
        keys = sorted({k for r in results for k in r})
        with args.out.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(results)
        print(f"\nwrote results -> {args.out}")

    # save the repaired-code CSV (replication)
    if codes:
        fields = ["Design", "Defect_Category", "Defect_Id", "Detectable_Without_Yosys",
                  "Status", "Accepted", "Final_Compile_OK", "Final_Equiv_Pass",
                  "Area", "Cells", "Slack", "Iterations", "Tokens", "Defective_Code", "Repaired_Code"]
        with args.codes_out.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(codes)
        print(f"wrote repaired codes -> {args.codes_out}")

    # summary
    acc = sum(1 for r in results if r.get("accepted"))
    det = sum(1 for r in results if r.get("ingest_compile_ok") is False or r.get("ingest_equiv_pass") is False)
    print(f"\n== {len(results)} examples | detected-at-ingest={det} | accepted-after-repair={acc} ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())