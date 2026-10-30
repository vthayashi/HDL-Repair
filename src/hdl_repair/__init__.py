"""hdl_repair — Automatic repair of LLM-generated HDL for synthesizability.

A PAIR-style iterative loop (ingest -> gate -> analyze -> repair) that takes an
LLM-generated RTL file, and repairs it until it is synthesizable AND functionally
equivalent to a golden/base reference design, collecting synthesis (PPA) metrics.

Roles: Ingestor (evaluate the input as-is), Synthesizability analyst (diagnose
failures into repair actions) and Repairer (rewrite the module).
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
