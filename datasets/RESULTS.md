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

## Execution time

Measured per step for every iteration (`datasets/iterations.csv`, built by
`tools/summarize_runs.py`; see [How timings are measured](#how-timings-are-measured)).
Machine: Windows 11, OSS CAD Suite 2026-10-30 (Icarus Verilog 14, Yosys 0.70),
OpenSTA 3.1.0 under WSL2; LLMs through the Hugging Face Inference Router.

### Per example

| # | Module | Outcome | Iter. | Gates (s) | Repair LLM (s) | Analyst LLM (s) | Total (s) | LLM share |
|---|---|---|:-:|--:|--:|--:|--:|--:|
| 0 | aes_sbox.v | repaired | 2 | 18.9 | 148.9 | 103.6 | 271.5 | 93 % |
| 1 | aes_inv_sbox.v | repaired | 2 | 18.4 | 203.8 | 75.1 | 297.3 | 94 % |
| 2 | aes_key_mem.v | repaired | 2 | 20.3 | 164.4 | 48.2 | 232.9 | 91 % |
| 3 | aes_core.v | repaired | 2 | 18.3 | 117.6 | 53.7 | 189.6 | 90 % |
| 4 | aes.v | repaired | 2 | 19.4 | 81.0 | 53.6 | 153.9 | 87 % |
| 5 | aes_encipher_block.v | repaired | 2 | 19.5 | 158.6 | 86.9 | 265.0 | 93 % |
| 6 | aes_decipher_block.v | not equivalent | 3 | 39.1 | 399.4 | 185.4 | 623.9 | 94 % |
| 7 | aes_encipher_block.v | repaired | 2 | 17.4 | 202.9 | 116.9 | 337.1 | 95 % |
| 8 | aes_inv_sbox.v | repaired | 2 | 18.1 | 278.3 | 122.5 | 418.8 | 96 % |
| 9 | aes_sbox.v | repaired | 2 | 30.7 | 155.4 | 32.7 | 218.8 | 86 % |
| 10 | aes_key_mem.v | undetected | 1 | 13.3 | — | — | 13.3 | 0 % |
| 11 | aes_core.v | repaired | 2 | 32.8 | 86.7 | 64.5 | 184.0 | 82 % |
| | **Total** | | | **266.2** | **1,996.9** | **943.1** | **3,206.1** (53.4 min) | **92 %** |

A successful repair takes **2.6–7.0 min** end to end (median 4.2 min over the 10
repairs). The gates cost ~19 s per full iteration; the rest is LLM generation.

### By step

| Step | Calls | Total (s) | Mean (s) | Range (s) | Share of run time |
|---|--:|--:|--:|--:|--:|
| Repair LLM (DeepSeek-V3) | 12 | 1,996.9 | 166.4 | 81.0–278.3 | 62.3 % |
| Analyst LLM (DeepSeek-R1) | 13 | 943.1 | 72.5 | 32.7–122.5 | 29.4 % |
| Yosys synthesis | 24 | 176.7 | 7.4 | 0.05–13.0 | 5.5 % |
| OpenSTA | 14 | 76.2 | 5.4 | 1.3–6.8 | 2.4 % |
| Simulation (`vvp`, golden testbench) | 17 | 8.6 | 0.5 | 0.06–0.8 | 0.3 % |
| Compilation (`iverilog`) | 24 | 4.5 | 0.2 | 0.13–0.83 | 0.1 % |

### Tool runtime per gate

Over the 14 iterations that ran the full flow (compile → simulate → synthesize
→ time) on the whole `aes` design (~18.7k cells):

| Gate | Median (s) | Range (s) |
|---|--:|--:|
| Compilation (iverilog) | 0.14 | 0.13–0.83 |
| Simulation (25 test cases) | 0.51 | 0.39–0.82 |
| Synthesis (Yosys → sky130, ABC) | 11.62 | 10.84–12.97 |
| Static timing (OpenSTA) | 6.04 | 1.33–6.82 |
| **All gates** | **≈18.3** | |

Failing candidates are cheaper: a syntax error stops Yosys within 0.05–0.4 s and
skips STA. OpenSTA times include starting the WSL process that hosts it.

## LLM usage

| Role | Calls | Usable replies | Tokens / call | Time / call (s) |
|---|--:|--:|--:|--:|
| Repairer (DeepSeek-V3) | 12 | 12 (100 %) | 8,786 (5,295–11,152) | 166 (81–278) |
| Analyst (DeepSeek-R1) | 13 | 7 (54 %) | 5,993 when usable | 86 usable / 57 empty |

- **Every repairer call returned a complete, structurally valid module**, and 10 of
  the 12 produced an accepted design on the first attempt.
- **The analyst returned an empty answer in 6 of 13 calls** (rows 2, 3, 4 and all
  three iterations of 6). The loop then falls back to the deterministic analyst,
  which derives repair actions from the gate taxonomy, so no repair was lost, but
  ~5.7 min were spent waiting on empty replies. The likely cause is R1's reasoning
  exhausting `HF_MAX_TOKENS_ANALYST` (1,200) before any answer text; their tokens
  are not included in the totals above.

## Defect classification at ingest

Categories reported by the gate taxonomy (`classify_errors`) on the unmodified
input, against the injected defect:

| # | Injected category | Failing gates | Reported categories | Expected reported? |
|---|---|---|---|:-:|
| 0 | late-syntax-error | C S E | late-syntax-error, undefined-module | ✓ |
| 1 | late-syntax-error | C S E | late-syntax-error, undefined-module | ✓ |
| 2 | undefined-module | C S E | undefined-module, late-syntax-error | ✓ |
| 3 | undefined-module | C S E | late-syntax-error, undefined-module | ✓ |
| 4 | non-synthesizable-construct | S E | sim-only-system-task, undefined-module | ~ (`$finish`) |
| 5 | non-synthesizable-construct | S | undefined-module | ✗ |
| 6 | late-syntax-error | C S E | late-syntax-error, undefined-module, invalid-nesting | ✓ |
| 7 | undefined-module | C S E | late-syntax-error, undefined-module | ✓ |
| 8 | invalid-nesting | C S E | late-syntax-error, undefined-module | ✗ |
| 9 | functional-equivalence | E | sim-only-system-task, late-syntax-error | n/a |
| 10 | non-synthesizable-construct | — | — | n/a |
| 11 | sim-only-system-task | S | sim-only-system-task | ✓ |

C = compile, S = synthesis, E = equivalence. The expected category is reported in
**7 of the 10** classifiable cases (8 counting `$finish` as a simulation-only
task). Precision is low: `undefined-module` appears in 9 of 11 detected cases,
and functional mismatches pick up spurious categories from testbench output.
Classification only steers the repair prompt, so this did not prevent repairs, but
it is the clearest target for improving the analyst.

### How timings are measured

New runs record each step's wall-clock time in `repair_iterations.jsonl`
(`timings`). The run above predates that field, so `tools/summarize_runs.py`
reconstructs the steps from the timestamps of the files each one writes (iteration
directory → candidate → `equiv.sim` → `sim.log` → `run_sta.tcl` → `opensta.log` →
next iteration). The reconstruction matches the console log to within a second
(e.g. row 0: 271.5 s vs 272 s).

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
| `results.csv` | per-example gate results, PPA, tokens, wall-clock and run directory for the run above |
| `iterations.csv` | one row per iteration: gate results, analyst source, tokens and per-step timings (`tools/summarize_runs.py`) |
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
python tools/summarize_runs.py --results datasets/results.csv --out datasets/iterations.csv
```

LLM outputs are sampled (temperature 0.2), so individual repairs can differ
between runs.
