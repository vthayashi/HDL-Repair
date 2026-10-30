"""Synthesizability analyst.

Given the candidate RTL and the combined compile/synthesis/simulation logs, the
analyst produces a structured verdict and concrete, synthesis-oriented repair
actions. An LLM (DeepSeek-R1 by default) is used when available; otherwise a
deterministic analyst derives actions from the error taxonomy so the loop still
functions without network/LLM access.
"""
from __future__ import annotations

import json
import re
import textwrap
from typing import Any

from .config import RepairConfig
from .llm_client import LLMClient, build_model_chain

_SYSTEM = (
    "You are a strict RTL synthesis analyst. You diagnose why a Verilog module "
    "fails to compile, synthesize, or match its golden testbench, and you return "
    "concise, actionable fixes. Be structured. Do not invent behavior."
)

# Deterministic repair hints keyed by taxonomy category.
_HINTS = {
    "undefined-module": "Resolve undefined module references: include/define every instantiated submodule or remove the instantiation; check the module name spelling matches the golden interface.",
    "non-synthesizable-construct": "Replace non-synthesizable constructs (delays #, real, fork/join, initial-driven logic) with synthesizable equivalents; keep only synthesizable Verilog-2001/2012.",
    "sim-only-system-task": "Remove simulation-only system tasks ($display/$finish/$dumpvars/$random) from the RTL module; they belong in the testbench, not the design.",
    "invalid-nesting": "Fix block structure: no declarations inside unnamed blocks, match begin/end, no premature end/endmodule.",
    "late-syntax-error": "Fix the syntax error reported in the log (malformed assignment l-value, missing semicolon, unexpected token) at the indicated line.",
    "synthesis-timeout": "Reduce synthesis complexity: avoid huge unrolled loops or combinational explosions that stall elaboration/mapping.",
    "tooling-missing": "A required tool is missing in the environment; this is not an RTL defect.",
}


def _deterministic(categories: list[str], compile_ok: bool, synthesizable: bool,
                   equiv_pass: bool) -> dict[str, Any]:
    actions = [_HINTS[c] for c in categories if c in _HINTS]
    if not compile_ok and not actions:
        actions.append("Make the module compile: ensure valid Verilog syntax and a complete module ... endmodule.")
    if compile_ok and not synthesizable and not actions:
        actions.append("Make the module synthesizable: remove constructs Yosys cannot map and keep the interface intact.")
    if compile_ok and not equiv_pass and not actions:
        actions.append("Preserve golden behavior: the design compiles but the golden testbench fails; align datapath/control so outputs match the reference without altering the module interface or reset semantics.")
    verdict = "PASS" if (compile_ok and synthesizable and equiv_pass) else "CONTINUE"
    return {
        "ok": True,
        "verdict": verdict,
        "synthesizable": synthesizable,
        "issues": categories,
        "repair_actions": actions[:6] or ["No specific issue detected; retry a minimal clean-up pass."],
        "source": "deterministic",
    }


def _build_user_prompt(candidate_rtl: str, log_tail: str, categories: list[str],
                       compile_ok: bool, synthesizable: bool, equiv_pass: bool) -> str:
    return textwrap.dedent(f"""
    Diagnose this RTL repair candidate and propose fixes.

    Gate status:
    - compile_ok: {compile_ok}
    - synthesizable: {synthesizable}
    - equivalent_to_golden: {equiv_pass}
    - detected_issue_categories: {categories}

    Tool log (tail):
    {log_tail[:4000]}

    Candidate RTL (excerpt):
    {candidate_rtl[:8000]}

    Return a JSON object only, with fields:
    - verdict: one of PASS | CONTINUE | FAIL
    - synthesizable: boolean
    - issues: array of short strings
    - repair_actions: array of short imperative fixes (most important first)
    Keep repair_actions concrete and limited to what the log/code justify. Do not
    change the module interface, port list, or reset semantics unless the log
    shows they are the defect.
    """).strip()


def analyze(client: LLMClient, cfg: RepairConfig, candidate_rtl: str, log_tail: str,
            categories: list[str], compile_ok: bool, synthesizable: bool,
            equiv_pass: bool) -> dict[str, Any]:
    """Return {verdict, synthesizable, issues, repair_actions, source, ...}."""
    if compile_ok and synthesizable and equiv_pass:
        return _deterministic(categories, compile_ok, synthesizable, equiv_pass)

    if not client.available:
        return _deterministic(categories, compile_ok, synthesizable, equiv_pass)

    chain = build_model_chain(cfg.model_analyst, cfg.model_analyst_fallbacks)
    user = _build_user_prompt(candidate_rtl, log_tail, categories, compile_ok,
                              synthesizable, equiv_pass)
    res = client.chat_with_fallbacks(chain, _SYSTEM, user, cfg.temperature_analyst,
                                     cfg.max_tokens_analyst)
    if not res.get("ok"):
        out = _deterministic(categories, compile_ok, synthesizable, equiv_pass)
        out["llm_error"] = res.get("error", "")
        return out

    text = (res.get("text") or "").strip()
    parsed = _parse_json(text)
    if parsed is None:
        out = _deterministic(categories, compile_ok, synthesizable, equiv_pass)
        out["llm_raw"] = text[:500]
        return out

    parsed.setdefault("verdict", "CONTINUE")
    parsed.setdefault("synthesizable", synthesizable)
    parsed.setdefault("issues", categories)
    if not parsed.get("repair_actions"):
        parsed["repair_actions"] = _deterministic(categories, compile_ok, synthesizable, equiv_pass)["repair_actions"]
    parsed["ok"] = True
    parsed["source"] = "llm"
    parsed["usage"] = res.get("usage", {})
    parsed["model"] = res.get("model", "")
    return parsed


def _parse_json(text: str) -> dict[str, Any] | None:
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None
