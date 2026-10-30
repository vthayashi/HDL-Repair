# Synthesis gate (Yosys / sky130)

The repair loop runs three gates per iteration: **compilation** (iverilog),
**synthesizability** (Yosys + sky130) and **equivalence** (golden testbench).
This document describes the synthesis gate, how to enable it, and how to
validate it with the bundled examples.

Golden model: `secworks/aes` (7 RTL modules). Synthesis top: `aes`.

## Where things are

| What | Path |
|---|---|
| Synthesis gate | `src/hdl_repair/synthesis.py` → `run_synthesis_gate()`, `_gen_yosys_script()` |
| How the loop calls it | `src/hdl_repair/pipeline.py` (`_evaluate`) |
| Golden RTL (7 modules, untouched) | `golden/aes/src/rtl/*.v` |
| Golden testbench (equivalence) | `golden/aes/src/tb/tb_aes.v` |
| Configuration | `.env.example` (copy to `.env`) |
| Batch runner | `tools/run_dataset.py` |
| Input dataset (12 defective AES variants) | `datasets/AES_defective_examples.csv` |
| Repaired code produced by the tool | `datasets/AES_repaired_examples.csv` |
| Repaired examples as `.v` files | `examples/repaired/` |
| Defects only synthesis can catch | `examples/synthesis_only_defects/` |
| Incomplete repair (compiles, not equivalent) | `examples/incomplete_repair/` |

## Prerequisites

- Python 3.10+ and `pip install -r requirements.txt`
- Icarus Verilog (`iverilog`, `vvp`) on `PATH`
- Yosys on `PATH`
- sky130 Liberty: `lib/sky130_fd_sc_hd__tt_025C_1v80.lib` (bundled)

Quick check: `iverilog -V` and `yosys -V` should both respond.

## Configuration

```bash
cp .env.example .env
```

```
RUN_SYNTHESIS=1
SYNTH_TIMEOUT_SEC=1800
# SKY130_LIB=/path/to/sky130_fd_sc_hd__tt_025C_1v80.lib   # only to override lib/
```

An `HF_TOKEN` is only needed for the full repair loop. Validating the gate itself
does not require one (see below).

## How the gate works

`run_synthesis_gate()` in `src/hdl_repair/synthesis.py`:

1. Checks that `yosys` is on `PATH` and the Liberty file exists. If not, it
   returns `available=False` and **does not block** the loop.
2. Generates a `.ys` script (`_gen_yosys_script`) that reads **all 7 RTL files**
   (with the target module replaced by the candidate), then runs
   `hierarchy -check -top aes`, `proc/opt/flatten/memory/techmap`, `dfflibmap`,
   `abc -liberty ...` and `stat`, yielding area and cell count.
3. Sets `synthesizable = True` if elaboration and mapping succeed and a netlist
   is produced.
4. On failure, classifies the log (`classify_errors`) to guide the repair.

When Yosys is available, the ACCEPT criterion requires
**compiles + equivalent + synthesizable**. Cases that would otherwise pass for
lack of a synthesis check now fail and trigger repair.

## Validation (no LLM token needed)

`examples/synthesis_only_defects/` contains defective AES modules that **compile
and pass the testbench** but are **not suitable for synthesis**: an `initial`
block with a local declaration, a `#10` delay, and `$display` inside the DUT.
Without the synthesis gate the tool accepts them.

Run the dataset in ingest-only mode; iteration 1 already runs the synthesis gate:

```bash
PYTHONPATH=src python tools/run_dataset.py \
    --csv datasets/AES_defective_examples.csv \
    --no-llm --max-iter 1 --out datasets/results_yosys_check.csv
```

Expected: `synth_verified` is `True` for every row (the gate ran). Compare with
`datasets/results.csv` (no Yosys) and `datasets/results_yosys_ingest.csv` (a
previous ingest-only run with Yosys enabled).

> **Note.** Yosys accepts some simulation-only constructs (it ignores `#` delays
> and `$display`), so not every case in `synthesis_only_defects/` is rejected by
> the stock flow. Catching them reliably needs extra checks, for example a lint
> pass (`verilator --lint-only`) or Yosys `read_verilog -sv` with stricter options.

Standalone sanity check (synthesize the untouched golden design):

```bash
yosys -p "read_verilog golden/aes/src/rtl/*.v; \
          read_liberty -lib lib/sky130_fd_sc_hd__tt_025C_1v80.lib; \
          hierarchy -check -top aes; proc; opt; flatten; memory; opt; \
          techmap; opt; dfflibmap -liberty lib/sky130_fd_sc_hd__tt_025C_1v80.lib; \
          abc -liberty lib/sky130_fd_sc_hd__tt_025C_1v80.lib -D 1000; \
          stat -liberty lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
```

This should synthesize without errors and print area and cell count: the golden
baseline. `tools/sanity_check.py` runs the same flow plus OpenSTA.

## Full loop (requires `HF_TOKEN`)

```bash
PYTHONPATH=src python tools/run_dataset.py \
    --csv datasets/AES_defective_examples.csv --max-iter 3
```

Non-synthesizable cases then follow: Yosys rejects → analyst classifies →
repairer rewrites → Yosys re-checks. Per-run artifacts are in
`runs/run_<timestamp>/iter_NN/` (`synth/synth.ys`, `area_report.txt`,
`netlist.v`, logs), with a summary in `repair_summary.json`.

## PPA metrics

`synthesis.py` extracts **area** and **cell count** (`parse_area_report`) and
computes `area_overhead_pct` against the golden baseline when
`BASELINE_AREA_REPORT` is set. Timing slack (`parse_sta_report`) is filled in when
OpenSTA is available; set `BASELINE_STA_REPORT` to compare against the golden.
