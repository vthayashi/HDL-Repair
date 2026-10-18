"""Command-line entry point.

Usage:
    python -m hdl_repair --input cases/aes/some_llm_version.v
    python -m hdl_repair --input X.v --design aes --target-rtl aes_core.v --max-iter 8
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import aes_design, load_config
from .pipeline import RepairPipeline
from .rtl_io import module_name_of


def _infer_target_rtl(spec_files: list[str], input_rtl: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    text = input_rtl.read_text(encoding="utf-8", errors="ignore")
    mod = module_name_of(text)
    if mod:
        cand = f"{mod}.v"
        if cand in spec_files:
            return cand
    # fall back to matching the file name against the design's files
    if input_rtl.name in spec_files:
        return input_rtl.name
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hdl_repair",
                                 description="Repair LLM-generated HDL for synthesizability (PAIR-style).")
    ap.add_argument("--input", required=True, type=Path, help="LLM-generated RTL file to repair")
    ap.add_argument("--design", default="aes", choices=["aes"], help="golden/base design (v1: aes)")
    ap.add_argument("--target-rtl", default=None,
                    help="which module file the candidate replaces (inferred if omitted)")
    ap.add_argument("--max-iter", type=int, default=None, help="override max iterations")
    ap.add_argument("--no-synth", action="store_true", help="skip the Yosys synthesizability gate")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not args.input.exists():
        print(f"error: input not found: {args.input}", file=sys.stderr)
        return 2

    cfg = load_config()
    if args.max_iter is not None:
        cfg.max_iterations = args.max_iter
    if args.no_synth:
        cfg.run_synthesis = False

    # v1: only the AES design is wired.
    aes_files = [
        "aes_core.v", "aes.v", "aes_key_mem.v", "aes_encipher_block.v",
        "aes_inv_sbox.v", "aes_sbox.v", "aes_decipher_block.v",
    ]
    target = _infer_target_rtl(aes_files, args.input, args.target_rtl)
    if not target:
        print("error: could not infer --target-rtl; pass it explicitly "
              f"(one of {aes_files})", file=sys.stderr)
        return 2
    spec = aes_design(target_rtl=target)

    if not spec.rtl_dir.exists():
        print(f"error: golden design not found at {spec.rtl_dir}. "
              "Clone secworks/aes into golden/aes first.", file=sys.stderr)
        return 2

    pipe = RepairPipeline(cfg, spec, verbose=not args.quiet)
    summary = pipe.run(args.input)
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("accepted") else 1


if __name__ == "__main__":
    raise SystemExit(main())
