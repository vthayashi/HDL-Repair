# Results — repairing defective AES

Produced by `tools/run_dataset.py` on `datasets/AES_defective_examples.csv`.
Golden: `golden/aes` (secworks/aes). Active gates: compilation (iverilog) and
equivalence (golden testbench). The synthesis gate (Yosys/sky130) was not enabled
for this run; see [`docs/synthesis_gate.md`](../docs/synthesis_gate.md).

| # | Module | Injected defect | Detectable without Yosys | Outcome | Iter. |
|---|---|---|---|---|---|
| 0 | aes_sbox.v | syntax error | yes | REPAIRED (compiles + equivalent) | 2 |
| 1 | aes_inv_sbox.v | syntax error | yes | REPAIRED (compiles + equivalent) | 2 |
| 2 | aes_key_mem.v | undefined module/signal | yes | REPAIRED (compiles + equivalent) | 2 |
| 3 | aes_core.v | undefined module/signal | yes | REPAIRED (compiles + equivalent) | 2 |
| 4 | aes.v | non-synthesizable construct | yes | REPAIRED (compiles + equivalent) | 2 |
| 5 | aes_encipher_block.v | non-synthesizable construct | no | passes current gates (synthesis-only) | 1 |
| 6 | aes_decipher_block.v | syntax error | yes | compiles, but not equivalent | 3 |
| 7 | aes_encipher_block.v | undefined module/signal | yes | REPAIRED (compiles + equivalent) | 3 |
| 8 | aes_inv_sbox.v | invalid nesting | yes | REPAIRED (compiles + equivalent) | 2 |
| 9 | aes_sbox.v | functional bug | yes | REPAIRED (compiles + equivalent) | 2 |
| 10 | aes_key_mem.v | non-synthesizable construct | no | passes current gates (synthesis-only) | 1 |
| 11 | aes_core.v | simulation-only task | no | passes current gates (synthesis-only) | 1 |

**Summary.** 12 defective AES examples. Of the 9 detectable without Yosys,
**8 were repaired** (compile again and match the golden testbench) and 1 compiled
but did not recover equivalence. The other 3 compile and pass the testbench; only
a synthesis-level check can flag them. Total LLM tokens: ~89k (DeepSeek-V3).

## Repaired code (`AES_repaired_examples.csv`)

Produced by the same script with `--codes-out`. For each example it contains the
defective input (`Defective_Code`), the repaired output (`Repaired_Code`), and
status, iteration and token counts.

This CSV covers the **8 smaller-module examples** (`aes`, `aes_core`,
`aes_key_mem`, `aes_encipher_block`, `aes_decipher_block`). The two S-box ROMs
(`aes_sbox.v` / `aes_inv_sbox.v`, 256-entry tables) are excluded because the LLM
must regenerate the whole table on every iteration, which makes runs slow; they
are covered in the 12-row table above.

Reproduce:

```bash
PYTHONPATH=src python tools/run_dataset.py --csv datasets/_fast_subset.csv \
    --max-iter 3 --out datasets/results_fast.csv \
    --codes-out datasets/AES_repaired_examples.csv
```

Outcome on this subset (8): **7 accepted** (4 genuine code repairs plus 3
`needs-yosys` cases that pass the current gates) and **1 not equivalent**
(`aes_decipher_block.v`, `missing_endmodule`: the LLM restored `endmodule` and
the code compiled, but regenerating the block changed its logic and broke
equivalence).

## Other result files

| File | Contents |
|---|---|
| `results.csv` | full 12-row run behind the table above |
| `results_fast.csv` / `_fast_subset.csv` | 8-row subset run (input + results) |
| `results_retry.csv` / `_retry_subset.csv` | re-run of failed rows |
| `results_llm.csv`, `results_all_examples.csv` | additional LLM runs |
| `results_yosys_ingest.csv` | ingest-only run (`--no-llm --max-iter 1`) with the Yosys gate enabled |
