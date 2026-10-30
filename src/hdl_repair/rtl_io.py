"""RTL extraction and structural validation of LLM output.
"""
from __future__ import annotations

import re


def extract_verilog_block(text: str) -> str:
    """Pull the Verilog out of a model reply (handles fenced ```verilog blocks)."""
    s = (text or "").strip()
    m = re.search(r"```\s*(?:verilog|systemverilog|v)?\s*\n([\s\S]*?)```", s, flags=re.I)
    if m:
        return m.group(1).strip() + "\n"
    if s.startswith("```"):
        s = re.sub(r"^```[^\n]*\n?", "", s, count=1)
    if s.endswith("```"):
        s = s[:-3]
    return s.strip() + "\n"


def module_name_of(rtl_text: str) -> str | None:
    """Return the first module name declared in the RTL, if any."""
    m = re.search(r"\bmodule\s+([A-Za-z_]\w*)", rtl_text or "")
    return m.group(1) if m else None


def validate_rtl_candidate(candidate_text: str, module_name: str, min_chars: int) -> list[str]:
    """Cheap structural checks before spending a compile/synthesis run."""
    errs: list[str] = []
    s = (candidate_text or "").strip()
    if not s:
        errs.append("empty candidate")
        return errs
    if "```" in s:
        errs.append("contains markdown fence markers")
    if f"module {module_name}" not in s:
        errs.append(f"missing module declaration for {module_name}")
    if "endmodule" not in s:
        errs.append("missing endmodule")
    if len(s) < int(min_chars):
        errs.append(f"candidate too short ({len(s)} chars < {int(min_chars)})")
    return errs
