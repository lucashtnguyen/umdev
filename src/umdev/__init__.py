"""Prepare utility evidence and explicit audited model updates.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Manually maintained public callable interface; project policy remains configuration.
"""

from importlib import import_module
from typing import Any

__version__ = "0.1.0"
__all__ = ["load_config", "ingest_sources", "prepare_network_from_dict", "run_project"]

_EXPORTS = {
    "load_config": ("configuration", "load_config"),
    "ingest_sources": ("sources", "ingest_sources"),
    "prepare_network_from_dict": ("parameters", "prepare_network_from_dict"),
    "run_project": ("pipeline", "run_project"),
}


def __getattr__(name: str) -> Any:
    """Load public functions without importing optional GIS drivers."""
    if name not in _EXPORTS:
        raise AttributeError(name)
    module, symbol = _EXPORTS[name]
    value = getattr(import_module(f".{module}", __name__), symbol)
    globals()[name] = value
    return value
