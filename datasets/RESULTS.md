# Results — repairing defective AES

Produced by `tools/run_dataset.py` on `datasets/AES_defective_examples.csv`
(12 defective variants of `secworks/aes`, one injected defect each).

**Setup.** All three gates active: compilation (Icarus Verilog), synthesizability
(Yosys → sky130 `sky130_fd_sc_hd__tt_025C_1v80`) and equivalence (golden
testbench `tb_aes`). Timing with OpenSTA at a 100 ns clock. Repairer
DeepSeek-V3, analyst DeepSeek-R1 (Hugging Face Inference Router), at most 3
iterations per example (1 ingest + 2 repairs).

## Per-example outcome

Ingest columns show which gate caught the defect before any repair.

| # | Module | Injected defect | Compiles | Synthesizes | Equivalent | Outcome | Iter. | Tokens |
|---|---|---|:-:|:-:|:-:|---|:-:|--:|
| 0 | aes_sbox.v | syntax error (bad l-value) | ✗ | ✗ | ✗ | **repaired** | 2 | 17,170 |
| 1 | aes_inv_sbox.v | syntax error (stray token) | ✗ | ✗ | ✗ | **repaired** | 2 | 17,294 |
| 2 | aes_key_mem.v | undefined module | ✗ | ✗ | ✗ | **repaired** | 2 | 7,987 |
| 3 | aes_core.v | undefined signal | ✗ | ✗ | ✗ | **repaired** | 2 | 5,691 |
| 4 | aes.v | `$finish` in the DUT | ✓ | ✗ | ✗ | **repaired** | 2 | 5,295 |
| 5 | aes_encipher_block.v | `initial` block with local declaration | ✓ | ✗ | ✓ | **repaired** | 2 | 14,468 |
| 6 | aes_decipher_block.v | missing `endmodule` | ✗ | ✗ | ✗ | compiles, not equivalent | 3 (no accept) | 18,789 |
| 7 | aes_encipher_block.v | use of undeclared signal | ✗ | ✗ | ✗ | **repaired** | 2 | 15,328 |
| 8 | aes_inv_sbox.v | invalid nesting (double `end`) | ✗ | ✗ | ✗ | **repaired** | 2 | 18,453 |
| 9 | aes_sbox.v | functional bug (XOR in table) | ✓ | ✓ | ✗ | **repaired** | 2 | 16,029 |
| 10 | aes_key_mem.v | `#10` delay | ✓ | ✓ | ✓ | undetected | 1 | 0 |
| 11 | aes_core.v | `$display` in the DUT | ✓ | ✗ | ✓ | **repaired** | 2 | 10,885 |

**Summary.**

- **10 of 12 repaired**: the output compiles, synthesizes to sky130 and passes the
  golden testbench. Every repair converged in a single repair iteration.
- **1 not equivalent** (#6). The LLM restored `endmodule` and the module compiles
  and synthesizes, but regenerating the decipher block altered its logic: the 10
  decryption test cases of the testbench fail.
- **1 undetected** (#10). Yosys silently drops `#` delays, the design is
  otherwise correct, so no gate fires. Catching it requires a lint pass (e.g.
  `verilator --lint-only`).
- **3 defects are invisible to simulation** (#5, #10, #11 compile and pass the
  testbench). The synthesis gate catches two of them (#5 at elaboration, #11 as
  an unmapped `$print` cell, see below).
- Tokens: 147k in total; 12.9k on average per repaired example (5.3k–18.5k; the
  256-entry S-box tables are the most expensive to regenerate).
- Wall-clock: ~55 min for the 12 examples, dominated by LLM generation
  (~3–5 min per full-module repair at ~15 tokens/s).

## PPA of the repaired designs

Golden baseline (whole `aes` top): area **167,613.25 µm²**, **18,707** cells,
worst slack **74.08 ns** at 100 ns.

| Outcome | Area overhead | Cells | Worst slack |
|---|---|---|---|
| 9 repaired examples | 0.000 % | 18,707 | 74.08 ns |
| #5 `aes_encipher_block` | −0.008 % | 18,726 | 73.91 ns |
| #6 (not equivalent, for reference) | −0.820 % | 18,272 | 79.39 ns |

For 9 of the 10 repairs, the synthesized netlist is identical in area and timing
to the golden: the repairer fixed the defect without perturbing the rest of the
module. Row #5's rewrite of the `initial` block yields a slightly different but
equivalent implementation. Row #6's smaller, "faster" netlist is a symptom of the
lost logic, not an improvement.

## Synthesis gate: unmapped cells

Yosys does not reject `$display`/`$write` inside a design. It keeps them as an
internal `$print` cell, finishes technology mapping and emits a netlist with a
behavioral `always` block among the standard cells. The gate now treats any
internal `$` cell left after mapping (other than `$scopeinfo` metadata) as
**not synthesizable** (`unmapped_cells()` in `src/hdl_repair/synthesis.py`).
Without that check, example #11 was accepted unchanged.

## Files

| File | Contents |
|---|---|
| `results.csv` | per-example gate results, PPA and tokens for the run above |
| `AES_repaired_examples.csv` | defective input (`Defective_Code`) and tool output (`Repaired_Code`) per example, with status, iterations and tokens |
| `AES_defective_examples.csv` | input dataset (built by `tools/make_aes_dataset.py`) |
| `results_fast.csv`, `results_retry.csv`, `results_llm.csv`, `results_all_examples.csv`, `results_yosys_ingest.csv` (+ `_fast_subset.csv`, `_retry_subset.csv`) | earlier runs without the synthesis/timing gates, kept for reference |

## Reproduce

Requires `HF_TOKEN` in `.env`, and `iverilog`, `yosys` and `sta` on `PATH` (see the
README). Write the golden PPA baseline first so area overhead and slack are
compared against it:

```bash
PYTHONPATH=src python tools/sanity_check.py      # writes runs/sanity_check_golden/
# .env: BASELINE_AREA_REPORT=runs/sanity_check_golden/area_report.txt
#       BASELINE_STA_REPORT=runs/sanity_check_golden/sta_report.txt
PYTHONPATH=src python tools/run_dataset.py --csv datasets/AES_defective_examples.csv \
    --max-iter 3 --out datasets/results.csv \
    --codes-out datasets/AES_repaired_examples.csv
```

LLM outputs are sampled (temperature 0.2), so individual repairs can differ
between runs.
