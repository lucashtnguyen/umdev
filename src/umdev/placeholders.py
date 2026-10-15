"""Explicit, auditable missing-rim placeholders for newly prepared junctions.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-15
Manually maintained. Placeholder elevations are assumptions, never observations.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping

from .records import Assignment
from .units import to_metres


def resolve_missing_rim(node: Mapping[str, Any], floor: Assignment, rim: Assignment,
                        config: Mapping[str, Any], *, is_existing: bool) -> Assignment:
    """Apply the configured missing-rim action to an eligible new junction.

    Parameters
    ----------
    node : mapping
        Canonical structure with original rim measurement provenance.
    floor, rim : Assignment
        Final selected profile values in metres on the target vertical basis.
    config : mapping
        Validated project configuration with an optional missing-rim policy.
    is_existing : bool
        Whether this structure already belongs to the baseline model.

    Returns
    -------
    Assignment
        Unchanged rim or an assumed placeholder retaining policy and floor evidence.

    Raises
    ------
    ValueError
        An eligible missing rim is encountered in error mode or the explicit
        placeholder contract is invalid.

    Notes
    -----
    Depth placeholders use rim = selected floor + positive placeholder depth.
    Unit "model" uses the declared target length unit so the configured value
    is the literal SWMM MaxDepth field. SWMM has no native absolute-rim field.
    Missing floors, existing
    structures, unresolved reviewed overrides, datum failures and malformed source
    rims are never repaired by this policy.
    """
    policy = config.get("parameters", {}).get("nodes", {}).get("missing_rim", {})
    mode = policy.get("mode", "hold")
    if mode not in {"hold", "error", "placeholder"}:
        raise ValueError("Missing-rim mode must be hold, error or placeholder")
    if mode == "hold" or is_existing or rim.value is not None:
        return rim
    if "rim" in node.get("_reviewed_override_holds", []) or node.get("_reviewed_overrides", {}).get("rim") is not None:
        return rim
    canonical_rim = node.get("rim")
    if canonical_rim is not None and not (isinstance(canonical_rim, str) and not canonical_rim.strip()):
        return rim
    provenance = node.get("measurement_provenance", {}).get("rim", {})
    raw = provenance.get("raw_value", node.get("rim"))
    if raw is not None and not (isinstance(raw, str) and not raw.strip()):
        return rim
    if rim.method not in {"missing_rim", "missing_elevation"}:
        return rim
    if mode == "error":
        raise ValueError(f"Missing rim elevation for new structure {node.get('key', node.get('asset_id', '<unknown>'))}")
    if floor.value is None:
        return rim
    placeholder = policy.get("placeholder", {})
    kind = placeholder.get("kind")
    if not isinstance(policy.get("basis"), str) or not policy["basis"].strip():
        raise ValueError("Missing-rim placeholder requires an explicit basis")
    if kind != "depth" or "vertical_datum" in placeholder:
        raise ValueError("Missing-rim placeholder requires datum-independent depth; SWMM has no native absolute-rim field")
    unit = placeholder.get("unit")
    resolved_unit = config.get("model", {}).get("target", {}).get("length_unit") if unit == "model" else unit
    quantity = to_metres(placeholder.get("value"), resolved_unit)
    if quantity <= 0:
        raise ValueError("Missing-rim placeholder depth must be positive")
    elevation = floor.value + quantity
    if not math.isfinite(elevation):
        raise ValueError("Missing-rim placeholder produces a nonfinite rim")
    flags = tuple(sorted(set(floor.flags) | {"placeholder_rim"}))
    return Assignment(elevation, "assumed", "missing_rim_placeholder",
        evidence=({"policy": deepcopy(policy), "floor_assignment": floor.to_dict(),
                   "replaced_rim_assignment": rim.to_dict(), "source_rim_raw": deepcopy(raw),
                   "target_vertical_datum": config.get("elevations", {}).get("target_datum") or config.get("model", {}).get("target", {}).get("vertical_datum"),
                   "is_placeholder": True, "placeholder_depth_m": quantity,
                   "resolved_length_unit": resolved_unit},), flags=flags)
