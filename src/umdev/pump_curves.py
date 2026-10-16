"""Explicit provisional pump head-flow preparation in SI units.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-16
Manually maintained pure calculations; no source curves or model files are edited.
"""
from __future__ import annotations
import math
from typing import Any

_METRES_PER_FOOT = 0.3048
_HW_VELOCITY_FACTOR_US = 1.318
_HW_VELOCITY_EXPONENT = 1.852
_HW_RADIUS_EXPONENT = 1.1667


def _finite(value: float, name: str, *, minimum: float | None=None,
            strictly_positive: bool=False) -> float:
    """Reject invalid dimensional quantities, including Boolean values."""
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError(name+' must be a finite number')
    result=float(value)
    if strictly_positive and result<=0 or minimum is not None and result<minimum:
        raise ValueError(name+' is outside its valid range')
    return result


def hazen_williams_full_pipe_headloss(length_m: float, diameter_m: float,
                                     flow_m3_s: float, coefficient: float) -> float:
    """Calculate full circular-pipe friction head using EPA SWMM H-W exponents.

    Parameters
    ----------
    length_m, diameter_m : float
        Positive physical length and inside diameter, metres.
    flow_m3_s : float
        Nonnegative operating flow magnitude, cubic metres per second.
    coefficient : float
        Positive dimensionless Hazen-Williams coefficient C, explicitly supplied.

    Returns
    -------
    float
        Friction head loss, metres. Zero flow has zero friction loss.

    Notes
    -----
    EPA5.2.4 forcmain.c uses velocity exponent1.852, hydraulic-radius exponent
    1.1667 and US velocity coefficient1.318. Convert inputs internally to feet:
    h_f=L*(v/(1.318*C))**1.852/R**1.1667, with R=D/4 and v=Q/(pi*D**2/4).
    This is the full-pipe empirical turbulent-water relation. It excludes minor
    losses, transients, air pockets and partly full flow; no C value is inferred.
    Source: https://raw.githubusercontent.com/USEPA/Stormwater-Management-Model/v5.2.4/src/solver/forcmain.c
    """
    length=_finite(length_m,'length_m',strictly_positive=True)
    diameter=_finite(diameter_m,'diameter_m',strictly_positive=True)
    flow=_finite(flow_m3_s,'flow_m3_s',minimum=0.)
    c=_finite(coefficient,'coefficient',strictly_positive=True)
    if flow==0.:return 0.
    try:
        length_ft=length/_METRES_PER_FOOT;diameter_ft=diameter/_METRES_PER_FOOT
        flow_ft3_s=flow/_METRES_PER_FOOT**3
        velocity_ft_s=flow_ft3_s/(math.pi*diameter_ft**2/4.)
        radius_ft=diameter_ft/4.
        head_m=length_ft*(velocity_ft_s/(_HW_VELOCITY_FACTOR_US*c))**_HW_VELOCITY_EXPONENT/radius_ft**_HW_RADIUS_EXPONENT*_METRES_PER_FOOT
    except (OverflowError,ZeroDivisionError) as error:
        raise ValueError('Hazen-Williams input exceeds floating-point calculation range') from error
    if not math.isfinite(head_m):raise ValueError('Nonfinite Hazen-Williams head loss')
    return head_m


def provisional_pump3_points(nominal_flow_m3_s: float, static_lift_m: float,
                              friction_head_m: float, duty_margin_m: float, *,
                              shutoff_head_factor: float=1.3,
                              runout_flow_factor: float=1.25) -> dict[str,Any]:
    """Prepare three explicitly synthetic SWMM PUMP3 head-flow points.

    Parameters
    ----------
    nominal_flow_m3_s : float
        Positive nominal rate evidence, not an assertion of measured capacity.
    static_lift_m : float
        Receiving target head minus representative wetwell water level, metres;
        downhill lifts may be negative.
    friction_head_m : float
        Nonnegative full-chain head loss at simultaneous assumed operating flow.
        Parallel pumps sharing a trunk require combined-flow friction.
    duty_margin_m : float
        Explicit positive provisional margin, metres; never silently defaulted.
    shutoff_head_factor, runout_flow_factor : float
        Explicit shape assumptions greater than1, default1.3 and1.25.

    Returns
    -------
    dict
        SI points ordered by increasing head, axes, assumptions and duty head.

    Notes
    -----
    Hn=max(0,static_lift+friction_head)+margin. Native PUMP3 X=head and Y=flow:
    (0,Qn*runout_factor), (Hn,Qn), (Hn*shutoff_factor,0).
    These monotone points are placeholders, not manufacturer curves, calibrated
    capacity, NPSH assessment or pressure/transient validation. Hydraulic writer
    and caller perform model-native unit conversion and preserve original data.
    """
    nominal=_finite(nominal_flow_m3_s,'nominal_flow_m3_s',strictly_positive=True)
    static=_finite(static_lift_m,'static_lift_m')
    friction=_finite(friction_head_m,'friction_head_m',minimum=0.)
    margin=_finite(duty_margin_m,'duty_margin_m',strictly_positive=True)
    shutoff=_finite(shutoff_head_factor,'shutoff_head_factor',minimum=1.)
    runout=_finite(runout_flow_factor,'runout_flow_factor',minimum=1.)
    if shutoff==1 or runout==1:raise ValueError('Curve shape factors must exceed1')
    head=max(0.,static+friction)+margin
    points=[{'head_m':0.,'flow_m3_s':nominal*runout},
            {'head_m':head,'flow_m3_s':nominal},
            {'head_m':head*shutoff,'flow_m3_s':0.}]
    if not all(math.isfinite(v) for point in points for v in point.values()):raise ValueError('Nonfinite provisional curve')
    return {'points':points,'duty_head_m':head,'nominal_flow_m3_s':nominal,
            'axes':{'x':'head_m','y':'flow_m3_s'},
            'assumptions':{'static_lift_m':static,'friction_head_m':friction,
                'duty_margin_m':margin,'shutoff_head_factor':shutoff,
                'runout_flow_factor':runout,'manufacturer_curve':False,
                'capacity_verified':False,'downhill_nominal_system_head':static+friction<0}}
