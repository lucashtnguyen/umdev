"""Canonical preparation snapshots and auditable scalar assignments.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Manually maintained. Engineering measurements use metres after ingestion.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence


def finite_number(value: Any) -> float | None:
    """Return a finite scalar, distinguishing missing values from measured zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class Network:
    """Copied canonical network independent of utility field names.

    Notes
    -----
    Rows are canonical mappings with SI engineering measurements and projected
    XY coordinates in metres. Pipe endpoint identities reference node asset IDs.
    A source-qualified key is separate from each utility asset ID. The dataclass
    freezes collection membership; mappings must be treated as read-only.
    """

    nodes: tuple[dict[str, Any], ...]
    pipes: tuple[dict[str, Any], ...]
    ports: tuple[dict[str, Any], ...] = ()
    manifest: Any = None
    findings: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Assignment:
    """Selected scalar plus its evidence, alternatives and qualifications."""

    value: float | None
    category: str
    method: str
    evidence: tuple[dict[str, Any], ...] = ()
    alternatives: tuple[dict[str, Any], ...] = ()
    flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable independent decision record."""
        return {"value": self.value, "unit": "m", "category": self.category,
                "method": self.method, "evidence": deepcopy(list(self.evidence)),
                "alternatives": deepcopy(list(self.alternatives)), "flags": list(self.flags)}


@dataclass(frozen=True)
class PreparedNetwork:
    """Prepared SI parameters, complete request dispositions and QA findings."""

    nodes: tuple[dict[str, Any], ...]
    pipes: tuple[dict[str, Any], ...]
    decisions: tuple[dict[str, Any], ...]
    findings: tuple[dict[str, Any], ...]
    held: tuple[dict[str, Any], ...]
    manifest: Any = None

    def to_dict(self) -> dict[str, Any]:
        """Return an independent JSON-serializable handoff for model adapters."""
        return deepcopy({"nodes": list(self.nodes), "pipes": list(self.pipes),
                         "decisions": list(self.decisions), "findings": list(self.findings),
                         "held": list(self.held), "manifest": self.manifest})


def network_from_dict(canonical: Mapping[str, Any]) -> Network:
    """Validate collection identities and copy an ingested canonical network.

    Parameters
    ----------
    canonical : mapping
        ``nodes``, ``pipes`` and optional ``ports`` sequences. Each row requires
        a unique nonempty source-qualified ``key`` and a separate ``asset_id``
        for nodes/pipes. Missing measurements remain missing, never zero-filled.

    Returns
    -------
    Network
        Independent preparation input retaining source metadata.

    Raises
    ------
    ValueError
        A collection, key or asset identity is missing/duplicated. Ambiguous
        utility identities are rejected before attributing any connection.
    """
    collections: dict[str, tuple[dict[str, Any], ...]] = {}
    for name in ("nodes", "pipes", "ports"):
        source = canonical.get(name, [])
        if not isinstance(source, Sequence) or isinstance(source, (str, bytes)):
            raise ValueError(f"Canonical {name} must be a sequence of mappings")
        rows = []
        keys: set[str] = set()
        assets: set[str] = set()
        for raw in source:
            if not isinstance(raw, Mapping):
                raise ValueError(f"Canonical {name} row must be a mapping")
            row = deepcopy(dict(raw))
            key = row.get("key")
            if not isinstance(key, str) or not key.strip() or key in keys:
                raise ValueError(f"Missing or duplicate {name} source key: {key!r}")
            keys.add(key)
            if name != "ports":
                identity = row.get("asset_id")
                if not isinstance(identity, str) or not identity.strip() or identity in assets:
                    raise ValueError(f"Missing or ambiguous {name} asset ID: {identity!r}")
                assets.add(identity)
            rows.append(row)
        collections[name] = tuple(sorted(rows, key=lambda row: row["key"]))
    return Network(collections["nodes"], collections["pipes"], collections["ports"],
                   deepcopy(canonical.get("manifest")),
                   tuple(deepcopy(canonical.get("findings", []))))
