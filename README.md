# HDL-Repair

**Automatic repair of LLM-generated HDL** so that it becomes **synthesizable** and
**functionally equivalent** to a reference (golden) design, using an iterative
**PAIR-style** loop: *ingest → test → analyze → repair*.

## Motivation

Recent work (arXiv:2603.11287, *Synthesis-in-the-Loop Evaluation of LLMs for RTL*)
shows that LLM-generated HDL is frequently not synthesizable. That evaluation is
limited to **single-turn, zero-shot** generation and leaves **iterative refinement
with synthesis feedback** as future work.

HDL-Repair addresses this gap and targets a common industrial setting: flows that
already have a **base implementation** (legacy code, maintenance, optimization).
A new, LLM-assisted version of a module is repaired until it is both
synthesizable and equivalent to the base design.

## How it works

For each run, given an input RTL file and a golden design:

1. **Ingest** (iteration 1): the input HDL is evaluated as-is, with no LLM call.
2. **Gates** (every iteration):
   - *Compilation* — `iverilog` compiles the design with the candidate replacing
     the target file. The golden tree is never modified.
   - *Synthesizability* — `yosys` elaborates the design and maps it to the
     sky130 standard-cell library. Optional: if Yosys is not installed, the gate
     is reported as *unverified* and does not block the loop.
   - *Equivalence* — the golden testbench (`tb_aes`) must pass.
3. **Analyst** (when a gate fails): classifies the failure using the taxonomy from
   the reference paper (late syntax error, undefined module, non-synthesizable
   construct, simulation-only task, invalid nesting) and emits concrete repair
   actions — via an LLM (DeepSeek-R1 by default), or deterministically when no
   LLM is available.
4. **Repairer** (DeepSeek-V3 by default): rewrites the module applying those
   actions while preserving the interface and reset semantics.
5. **Stop**: when the candidate compiles, synthesizes and is equivalent (ACCEPT),
   or when `MAX_ITERATIONS` / the token budget is reached.

```
          ┌──────────┐   pass   ┌────────┐
input ──▶ │  gates   │ ───────▶ │ ACCEPT │
          └────┬─────┘          └────────┘
               │ fail
               ▼
          ┌──────────┐  actions ┌──────────┐
          │ analyst  │ ───────▶ │ repairer │ ──▶ next iteration
          └──────────┘          └──────────┘
```

Each run writes its artifacts to `runs/run_<timestamp>/`: one `iter_NN/` folder per
iteration (logs, Yosys script, area report, netlist), `repair_iterations.jsonl`,
`repair_iterations.md`, `repair_summary.json`, and `repaired/<file>.v` (best
candidate).

## Installation

Requires **Python 3.10+**.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows (Git Bash): . .venv/Scripts/activate
pip install -e .                   # or: pip install -r requirements.txt
cp .env.example .env               # then set HF_TOKEN
```

External tools expected on `PATH`:

| Tool | Purpose | Required |
|---|---|---|
| `iverilog` + `vvp` | compilation and simulation (compile + equivalence gates) | yes |
| `yosys` | synthesizability gate, area and cell count | optional |
| `sta` (OpenSTA) | timing / slack | optional |

All three are available in the [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build).

The golden model ([secworks/aes](https://github.com/secworks/aes)) is vendored in
`golden/aes/`, and the sky130 Liberty file
(`sky130_fd_sc_hd__tt_025C_1v80.lib`, ~13 MB) in `lib/`. Use `SKY130_LIB` in `.env`
to point at a different copy.

## Usage

```bash
# repair a single file (target module inferred from the module name)
python -m hdl_repair --input cases/aes/defect_aes_key_mem.v

# explicit target and iteration budget
python -m hdl_repair --input X.v --target-rtl aes_core.v --max-iter 8

# skip the synthesis gate (compile + equivalence only)
python -m hdl_repair --input X.v --no-synth
```

Batch evaluation over a dataset:

```bash
# detection only (no LLM): runs every gate once per example
PYTHONPATH=src python tools/run_dataset.py \
    --csv datasets/AES_defective_examples.csv --no-llm --max-iter 1

# full repair loop
PYTHONPATH=src python tools/run_dataset.py \
    --csv datasets/AES_defective_examples.csv --max-iter 3 \
    --codes-out datasets/AES_repaired_examples.csv
```

Sanity-check the synthesis flow on the untouched golden design:

```bash
PYTHONPATH=src python tools/sanity_check.py
```

## Configuration

All settings are read from `.env` (or the process environment). See
[`.env.example`](.env.example) for the full list; the most relevant are:

| Variable | Default | Meaning |
|---|---|---|
| `HF_TOKEN` | — | Hugging Face token (Inference Router) for the LLM roles |
| `HF_MODEL_REPAIRER` / `HF_MODEL_ANALYST` | DeepSeek-V3 / DeepSeek-R1 | models per role |
| `MAX_ITERATIONS` | `8` | repair iterations per run |
| `MAX_TOTAL_TOKENS_EST` | `120000` | token budget per run |
| `RUN_SYNTHESIS` | `1` | enable the Yosys gate |
| `SKY130_LIB` | `lib/sky130_fd_sc_hd__tt_025C_1v80.lib` | Liberty file |
| `BASELINE_AREA_REPORT` / `BASELINE_STA_REPORT` | — | golden PPA reports for area-overhead scoring |

## Repository layout

```
src/hdl_repair/
  config.py      # DesignSpec, RepairConfig, aes_design()
  llm_client.py  # Hugging Face Inference Router client (retry/backoff)
  rtl_io.py      # RTL extraction and structural validation
  gates.py       # compile + equivalence gates, error taxonomy
  synthesis.py   # synthesizability gate (Yosys) + PPA parsing
  analyst.py     # diagnosis -> repair actions
  repairer.py    # LLM-based repair
  scoring.py     # repair quality score
  pipeline.py    # orchestration loop + logging
  cli.py         # command-line interface
tools/
  make_aes_dataset.py  # builds the defective-AES dataset (injected defects)
  run_dataset.py       # batch runner over a dataset CSV
  sanity_check.py      # Yosys + OpenSTA on the clean golden model
datasets/        # input dataset, results and repaired code (see RESULTS.md)
examples/        # selected input/output RTL files (see docs/synthesis_gate.md)
cases/aes/       # sample input files to repair
golden/aes/      # secworks/aes reference design
lib/             # sky130 Liberty file
docs/            # additional documentation
runs/            # per-run artifacts (git-ignored)
```

## Results

On 12 defective AES variants covering the five defect categories, the loop
repaired 8 of the 9 defects detectable by compilation/simulation alone. The
remaining 3 compile and pass the testbench but contain non-synthesizable or
simulation-only constructs; they are the target of the synthesis gate. See
[`datasets/RESULTS.md`](datasets/RESULTS.md) for details and reproduction steps.

## Scope and next steps

- v1 case study: **AES**. The architecture is design-agnostic (`DesignSpec`), so
  adding a design means describing its RTL files, testbench and top module.
- Planned: post-quantum designs (e.g. Kyber), FPGA flows (Vivado), and timing-aware
  scoring with OpenSTA.

## License

GPL-3.0 — see [`LICENSE`](LICENSE). The vendored `golden/aes` design and the sky130
Liberty file keep their original licenses.
