"""Configuration: environment loading, design specs, and the repair config.

A ``DesignSpec`` describes the base/golden design against which a candidate RTL
file is repaired and checked: where the RTL lives, which files compose it, the
testbench that defines functional equivalence, the top module for synthesis, and
the Liberty library + baseline metrics for PPA.

The tool is design-agnostic by construction; ``aes_design()`` builds the v1
case-study spec pointing at the freshly cloned secworks/aes golden model.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# HDL-Repair/src/hdl_repair/config.py  ->  parents[2] == HDL-Repair/
PROJECT_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = PROJECT_ROOT / "golden"
RUNS_DIR = PROJECT_ROOT / "runs"
CASES_DIR = PROJECT_ROOT / "cases"

# Default sky130 Liberty: reuse the copy already present from the AHA challenge
# (13 MB; not duplicated here). Override with SKY130_LIB in .env.
_AHA_LIB = (
    PROJECT_ROOT.parent
    / "AHA_Challenge_2026"
    / "aes"
    / "aes_synth"
    / "sky130_fd_sc_hd__tt_025C_1v80.lib"
)


def load_env_file(env_path: Path) -> dict[str, str]:
    """Minimal .env parser (reused from the AHA notebook)."""
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values


@dataclass
class DesignSpec:
    """A base/golden design the candidate RTL is repaired against."""

    name: str
    rtl_dir: Path
    rtl_files: list[str]            # ordered file names in rtl_dir (compile order)
    tb_file: Path                   # testbench defining functional equivalence
    top_module: str                 # top for synthesis
    tb_top_module: str              # top for simulation
    target_rtl: str                 # the rtl file the candidate replaces
    liberty: Optional[Path] = None  # sky130 Liberty for Yosys (optional)
    baseline_area_report: Optional[Path] = None
    baseline_sta_report: Optional[Path] = None

    def rtl_paths(self) -> list[Path]:
        return [self.rtl_dir / f for f in self.rtl_files]

    def target_path(self) -> Path:
        return self.rtl_dir / self.target_rtl


@dataclass
class RepairConfig:
    """Loop + LLM + tool configuration."""

    # LLM (HuggingFace Inference Router, reused from the AHA notebook)
    hf_token: Optional[str] = None
    hf_base_url: str = "https://router.huggingface.co/v1"
    hf_provider: str = "auto"
    model_repairer: str = "deepseek-ai/DeepSeek-V3"
    model_analyst: str = "deepseek-ai/DeepSeek-R1"
    model_repairer_fallbacks: list[str] = field(default_factory=list)
    model_analyst_fallbacks: list[str] = field(default_factory=list)
    max_tokens_repairer: int = 6000
    max_tokens_analyst: int = 1200
    temperature_repairer: float = 0.20
    temperature_analyst: float = 0.0

    # Network resiliency — tuned to fail fast so a flaky connection does not
    # stall a batch for minutes (a single call is bounded to a few seconds).
    hf_http_retries: int = 2
    hf_http_backoff_base_sec: float = 1.0
    hf_http_backoff_cap_sec: float = 6.0
    hf_http_timeout_sec: int = 45

    # Candidate quality
    max_candidate_gen_retries: int = 3
    min_rtl_chars: int = 500

    # Loop control
    max_iterations: int = 8
    max_total_tokens_est: int = 120_000

    # Tool flow
    sim_backend: str = "auto"          # auto | fusesoc | iverilog
    run_synthesis: bool = True         # synthesizability gate via Yosys
    run_ppa: bool = True               # collect area/timing metrics (needs yosys/sta)
    sim_timeout_sec: int = 90
    synth_timeout_sec: int = 1800


def load_config(env_dir: Optional[Path] = None) -> RepairConfig:
    """Build a RepairConfig from .env (if present) + process environment."""
    env_dir = env_dir or PROJECT_ROOT
    env = load_env_file(env_dir / ".env")

    def g(key: str, default: str = "") -> str:
        return env.get(key) or os.environ.get(key, default)

    def split_csv(val: str) -> list[str]:
        return [m.strip() for m in val.split(",") if m.strip()]

    cfg = RepairConfig(
        hf_token=g("HF_TOKEN") or None,
        hf_base_url=g("HF_BASE_URL", "https://router.huggingface.co/v1"),
        hf_provider=g("HF_PROVIDER", "auto"),
        model_repairer=g("HF_MODEL_REPAIRER", "deepseek-ai/DeepSeek-V3"),
        model_analyst=g("HF_MODEL_ANALYST", "deepseek-ai/DeepSeek-R1"),
        model_repairer_fallbacks=split_csv(g("HF_MODEL_REPAIRER_FALLBACKS", "")),
        model_analyst_fallbacks=split_csv(g("HF_MODEL_ANALYST_FALLBACKS", "")),
        max_tokens_repairer=int(g("HF_MAX_TOKENS_REPAIRER", "6000")),
        max_tokens_analyst=int(g("HF_MAX_TOKENS_ANALYST", "1200")),
        hf_http_retries=int(g("HF_HTTP_RETRIES", "2")),
        hf_http_backoff_base_sec=float(g("HF_HTTP_BACKOFF_BASE_SEC", "1.0")),
        hf_http_backoff_cap_sec=float(g("HF_HTTP_BACKOFF_CAP_SEC", "6.0")),
        hf_http_timeout_sec=int(g("HF_HTTP_TIMEOUT_SEC", "45")),
        max_candidate_gen_retries=int(g("MAX_CANDIDATE_GEN_RETRIES", "3")),
        min_rtl_chars=int(g("MIN_RTL_CHARS", "500")),
        max_iterations=int(g("MAX_ITERATIONS", "8")),
        max_total_tokens_est=int(g("MAX_TOTAL_TOKENS_EST", "120000")),
        sim_backend=g("RUN_SIM_BACKEND", "auto"),
        run_synthesis=g("RUN_SYNTHESIS", "1") not in ("0", "false", "False"),
        run_ppa=g("RUN_PPA", "1") not in ("0", "false", "False"),
        sim_timeout_sec=int(g("SIM_TIMEOUT_SEC", "90")),
        synth_timeout_sec=int(g("SYNTH_TIMEOUT_SEC", "1800")),
    )
    return cfg


def aes_design(target_rtl: str = "aes_key_mem.v") -> DesignSpec:
    """v1 case study: secworks/aes golden model (cloned in golden/aes).

    ``target_rtl`` is the single module file the candidate replaces; the other
    six RTL files come from the untouched golden clone.
    """
    rtl_dir = GOLDEN_DIR / "aes" / "src" / "rtl"
    tb_dir = GOLDEN_DIR / "aes" / "src" / "tb"
    # compile order mirrors aes.core filesets
    rtl_files = [
        "aes_core.v",
        "aes.v",
        "aes_key_mem.v",
        "aes_encipher_block.v",
        "aes_inv_sbox.v",
        "aes_sbox.v",
        "aes_decipher_block.v",
    ]
    env = load_env_file(PROJECT_ROOT / ".env")
    lib_override = env.get("SKY130_LIB") or os.environ.get("SKY130_LIB")
    liberty = Path(lib_override) if lib_override else _AHA_LIB
    aha_synth = PROJECT_ROOT.parent / "AHA_Challenge_2026" / "aes" / "aes_synth"
    return DesignSpec(
        name="aes",
        rtl_dir=rtl_dir,
        rtl_files=rtl_files,
        tb_file=tb_dir / "tb_aes.v",
        top_module="aes",
        tb_top_module="tb_aes",
        target_rtl=target_rtl,
        liberty=liberty if liberty.exists() else None,
        baseline_area_report=(aha_synth / "original_metrics" / "area_report.txt"),
        baseline_sta_report=(aha_synth / "original_metrics" / "clean_sta_report.txt"),
    )
