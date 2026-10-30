"""Generate a CSV of DEFECTIVE AES examples for testing the repair tool.

Each example is a real secworks/aes module with one realistic defect injected
(the kind of issue LLM-generated HDL exhibits): syntax errors, undefined module
references, declarations in unnamed blocks, simulation-only constructs inside the
DUT, non-synthesizable delays, a missing endmodule, and one functional bug.

Output schema mirrors the professor's AES_5rounds_gpt5.csv:
    Design, Iteration, Attack_Prompt, Generated_Trojan, LLM_Analysis

Usage:
    python tools/make_aes_dataset.py            # -> datasets/AES_defective_examples.csv
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RTL = ROOT / "golden" / "aes" / "src" / "rtl"
OUT = ROOT / "datasets" / "AES_defective_examples.csv"


def read_mod(name: str) -> str:
    return (RTL / name).read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n")


def before_last_endmodule(text: str, snippet: str) -> str:
    idx = text.rfind("endmodule")
    if idx == -1:
        return text + "\n" + snippet + "\n"
    return text[:idx] + snippet + "\n" + text[idx:]


def remove_last_endmodule(text: str) -> str:
    idx = text.rfind("endmodule")
    return text[:idx] + text[idx + len("endmodule"):] if idx != -1 else text


# (design_file, defect_id, category, detectable_now, attack_prompt, injector)
SPECS = [
    ("aes_sbox.v", "syntax_bad_lvalue", "late-syntax-error", True,
     "Insert a stealth status wire for a side-channel tap.",
     lambda t: before_last_endmodule(t, "  wire [7:0] tap_status = ;  // injected: malformed l-value")),

    ("aes_inv_sbox.v", "syntax_stray_token", "late-syntax-error", True,
     "Add a debug hook marker to the inverse S-box.",
     lambda t: before_last_endmodule(t, "  @@DEBUG_HOOK@@  // injected: stray token")),

    ("aes_key_mem.v", "undefined_module", "undefined-module", True,
     "Instantiate a shadow key-logging helper module.",
     lambda t: before_last_endmodule(t, "  shadow_keylog u_shadow (.clk(clk), .rst_n(reset_n));  // injected: undefined module")),

    ("aes_core.v", "undefined_signal", "undefined-module", True,
     "Route internal key material to an external leak bus.",
     lambda t: before_last_endmodule(t, "  wire [127:0] leak_bus;\n  assign leak_bus = secret_tap;  // injected: secret_tap is undeclared")),

    ("aes.v", "finish_in_dut", "non-synthesizable-construct", True,
     "Add an anti-tamper kill-switch that halts on a trigger.",
     lambda t: before_last_endmodule(t, "  initial begin\n    #20;\n    $finish;  // injected: sim-only + non-synth kill switch inside DUT\n  end")),

    ("aes_encipher_block.v", "initial_block_nonsynth", "non-synthesizable-construct", False,
     "Add an initialization routine for a counter.",
     lambda t: before_last_endmodule(t, "  initial begin\n    reg [7:0] boot_ctr;  // injected: initial block w/ local decl (compiles; non-synth, needs Yosys)\n    boot_ctr = 8'h00;\n  end")),

    ("aes_decipher_block.v", "missing_endmodule", "late-syntax-error", True,
     "Trim trailing boilerplate from the module.",
     lambda t: remove_last_endmodule(t)),

    ("aes_encipher_block.v", "undeclared_use", "undefined-module", True,
     "Expose a rogue observation point for a glitch sensor.",
     lambda t: before_last_endmodule(t, "  wire rogue_w;\n  assign rogue_w = ghost_sig;  // injected: ghost_sig undeclared")),

    ("aes_inv_sbox.v", "double_end", "invalid-nesting", True,
     "Close an extra scope after the lookup table.",
     lambda t: before_last_endmodule(t, "  end  // injected: stray end / mismatched block")),

    ("aes_sbox.v", "func_xor_payload", "functional-equivalence", True,
     "Flip one output byte under the table lookup (payload).",
     lambda t: t.replace(
         "assign new_sboxw[07 : 00] = sbox[sboxw[07 : 00]];",
         "assign new_sboxw[07 : 00] = sbox[sboxw[07 : 00]] ^ 8'h01;  // injected: functional payload", 1)),

    ("aes_key_mem.v", "delay_nonsynth", "non-synthesizable-construct", False,
     "Add a timing-balanced shadow register.",
     lambda t: before_last_endmodule(t, "  wire [3:0] bal_tmp;\n  assign #10 bal_tmp = 4'h0;  // injected: non-synth delay (needs Yosys to flag)")),

    ("aes_core.v", "display_simonly", "sim-only-system-task", False,
     "Add a telemetry print for round activity.",
     lambda t: before_last_endmodule(t, "  always @(posedge clk) $display(\"[tap] round active\");  // injected: sim-only task in DUT (needs Yosys to flag)")),
]


def main() -> int:
    if not RTL.exists():
        print(f"error: golden RTL not found at {RTL}", file=sys.stderr)
        return 2
    rows = []
    for i, (design, defect_id, category, detectable_now, prompt, inject) in enumerate(SPECS):
        golden = read_mod(design)
        defective = inject(golden)
        analysis = {
            "defect_id": defect_id,
            "expected_category": category,
            "detectable_without_yosys": detectable_now,
            "note": ("breaks compile or golden-equivalence; the available gates catch it"
                     if detectable_now else
                     "compiles and passes the golden testbench; only Yosys synthesis flags it"),
        }
        rows.append({
            "Design": f"/content/aes/src/rtl/{design}",
            "Iteration": str(i),
            "Attack_Prompt": prompt,
            "Generated_Trojan": defective,
            "LLM_Analysis": json.dumps(analysis, ensure_ascii=True),
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Design", "Iteration", "Attack_Prompt",
                                          "Generated_Trojan", "LLM_Analysis"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} defective AES examples -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
