"""Prepare utility evidence and explicit audited model updates.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-16
Manually maintained public callable interface; project policy remains configuration.
"""

from importlib import import_module
from typing import Any

__version__ = "0.1.0"
__all__ = ["load_config", "ingest_sources", "prepare_network_from_dict", "prepare_project", "run_project", "infer_missing_inverts", "simplify_short_pipes", "observation_fingerprint", "propose_missing_pipe_end_offsets", "relative_port_offset_plan", "hazen_williams_full_pipe_headloss", "provisional_pump3_points"]

_EXPORTS = {
    "load_config": ("configuration", "load_config"),
    "ingest_sources": ("sources", "ingest_sources"),
    "prepare_network_from_dict": ("parameters", "prepare_network_from_dict"),
    "prepare_project": ("pipeline", "prepare_project"),
    "run_project": ("pipeline", "run_project"),
    "infer_missing_inverts": ("network_methods", "infer_missing_inverts"),
    "simplify_short_pipes": ("network_methods", "simplify_short_pipes"),
    "observation_fingerprint": ("observation_holds", "observation_fingerprint"),
    "propose_missing_pipe_end_offsets": ("pipe_end_offsets", "propose_missing_pipe_end_offsets"),
    "relative_port_offset_plan": ("relative_port_offsets", "relative_port_offset_plan"),
    "hazen_williams_full_pipe_headloss": ("pump_curves", "hazen_williams_full_pipe_headloss"),
    "provisional_pump3_points": ("pump_curves", "provisional_pump3_points"),
}


def __getattr__(name: str) -> Any:
    """Load public functions without importing optional GIS drivers."""
    if name not in _EXPORTS:
        raise AttributeError(name)
    module, symbol = _EXPORTS[name]
    value = getattr(import_module(f".{module}", __name__), symbol)
    globals()[name] = value
    return value
