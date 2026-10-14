"""Explicit engineering length conversion for canonical SI observations.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Manually maintained reusable utility-model preparation component.
"""

from __future__ import annotations

import math
from numbers import Real

# International foot is exactly 0.3048 m. US survey foot is exactly 1200/3937 m.
LENGTH_FACTORS = {"m": 1.0, "mm": 0.001, "in": 0.0254, "ft": 0.3048,
                  "us_survey_ft": 1200.0 / 3937.0}


def to_metres(value: object, unit: str) -> float:
    """Convert a finite length measurement into metres.

    Parameters
    ----------
    value : object
        Finite real number or ASCII numeric text; booleans are rejected.
    unit : str
        Explicit supported length unit. No datum transformation is performed.

    Returns
    -------
    float
        Length in metres.

    Raises
    ------
    ValueError
        If the unit or numeric value is unsupported.
    """
    if unit not in LENGTH_FACTORS:
        raise ValueError(f"Unsupported length unit: {unit!r}")
    if isinstance(value, bool) or not isinstance(value, (Real, str)):
        raise ValueError(f"Expected finite real measurement, received {value!r}")
    if isinstance(value, str) and (not value.isascii() or "_" in value):
        raise ValueError(f"Expected ASCII numeric measurement, received {value!r}")
    try:
        result = float(value) * LENGTH_FACTORS[unit]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Invalid measurement: {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"Measurement must be finite: {value!r}")
    return result


def from_metres(value: object, unit: str) -> float:
    """Convert a finite metre value to an explicitly declared length unit."""
    if unit not in LENGTH_FACTORS:
        raise ValueError(f"Unsupported length unit: {unit!r}")
    return to_metres(value, "m") / LENGTH_FACTORS[unit]
