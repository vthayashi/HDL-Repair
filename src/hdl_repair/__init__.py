"""hdl_repair — Automatic repair of LLM-generated HDL for synthesizability.

A PAIR-style iterative loop (ingest -> gate -> analyze -> repair) that takes an
LLM-generated RTL file, and repairs it until it is synthesizable AND functionally
equivalent to a golden/base reference design, collecting synthesis (PPA) metrics.

The engine is re-framed from the AHA Challenge 2026 trojan-generation notebook:
the roles are Ingestor / Repairer / Synthesizability-analyst (no CVSS/exploit).
"""

from .config import DesignSpec, RepairConfig, load_config, aes_design
from .pipeline import RepairPipeline, IterationRecord

__all__ = [
    "DesignSpec",
    "RepairConfig",
    "load_config",
    "aes_design",
    "RepairPipeline",
    "IterationRecord",
]

__version__ = "0.1.0"
