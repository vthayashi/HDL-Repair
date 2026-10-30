"""Repairer: LLM-driven module rewrite.

Builds the repair prompt from the current candidate, the analyst's actions, and
the tool log tail, then asks the LLM for a complete, corrected module. The
contract is strict: keep the module interface and intended behavior, change only
what the diagnosis requires, return one complete fenced Verilog module.
"""
from __future__ import annotations

import textwrap
from typing import Any

from .config import DesignSpec, RepairConfig
from .llm_client import LLMClient, build_model_chain
from .rtl_io import extract_verilog_block, validate_rtl_candidate

SYSTEM_PROMPT = (
    "You are an expert RTL engineer specializing in making Verilog synthesizable "
    "while preserving behavior. Return only one complete, valid Verilog module "
    "inside a single fenced ```verilog block. No explanations."
)


def build_repair_prompt(spec: DesignSpec, candidate_rtl: str,
                        analyst_feedback: dict[str, Any], log_tail: str) -> str:
    actions = analyst_feedback.get("repair_actions", []) or []
    actions_txt = "\n".join(f"- {a}" for a in actions[:8]) or "- Fix the issues shown in the log."
    issues = analyst_feedback.get("issues", []) or []
    return textwrap.dedent(f"""
    Repair the Verilog module `{spec.target_rtl}` of the `{spec.name}` design so it
    is SYNTHESIZABLE and functionally EQUIVALENT to the golden reference.

    Hard constraints:
    - Preserve the module name, port list, parameters and reset semantics exactly.
    - Change only what is needed to fix the reported defects; keep edits minimal and local.
    - The module is part of a larger design; do not add/remove ports or rename signals
      visible at the interface.
    - Output must be synthesizable Verilog (no simulation-only system tasks, no delays,
      no constructs a standard-cell synthesis flow rejects).

    Detected issue categories: {issues}

    Required fixes (from the synthesis analyst):
    {actions_txt}

    Tool log (tail):
    {log_tail[:3000]}

    Current candidate `{spec.target_rtl}`:
    {candidate_rtl[:14000]}

    Return the complete corrected `{spec.target_rtl}` in one ```verilog fenced block only.
    """).strip()


def repair(client: LLMClient, cfg: RepairConfig, spec: DesignSpec,
           candidate_rtl: str, analyst_feedback: dict[str, Any],
           log_tail: str, module_name: str) -> dict[str, Any]:
    """Ask the LLM for a corrected module; validate structure before returning.

    Returns {ok, rtl, errors, usage, model, llm_error}.
    """
    if not client.available:
        return {"ok": False, "rtl": candidate_rtl, "errors": ["LLM unavailable (no HF_TOKEN)"],
                "usage": {}, "model": ""}

    chain = build_model_chain(cfg.model_repairer, cfg.model_repairer_fallbacks)
    prompt = build_repair_prompt(spec, candidate_rtl, analyst_feedback, log_tail)

    last: dict[str, Any] = {"ok": False, "error": "not called"}
    for _ in range(max(1, cfg.max_candidate_gen_retries)):
        res = client.chat_with_fallbacks(chain, SYSTEM_PROMPT, prompt,
                                         cfg.temperature_repairer, cfg.max_tokens_repairer)
        last = res
        if not res.get("ok"):
            if res.get("transient"):
                continue
            return {"ok": False, "rtl": candidate_rtl, "errors": [res.get("error", "llm error")],
                    "usage": {}, "model": res.get("model", "")}
        rtl = extract_verilog_block(res.get("text", ""))
        errs = validate_rtl_candidate(rtl, module_name, cfg.min_rtl_chars)
        if errs:
            continue
        return {"ok": True, "rtl": rtl, "errors": [], "usage": res.get("usage", {}),
                "model": res.get("model", ""), "prompt": prompt}

    return {"ok": False, "rtl": candidate_rtl,
            "errors": ["could not obtain a structurally valid repaired module"],
            "usage": last.get("usage", {}), "model": last.get("model", ""),
            "llm_error": last.get("error", "")}
