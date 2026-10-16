# Provisional pump head-flow preparation

Author: Lucas HT Nguyen  
Contact: https://github.com/lucashtnguyen  
Date: 2026-10-16

## Summary

`umdev.pump_curves` provides pure SI calculations for explicitly synthetic SWMM
PUMP3 curves. It does not alter original observations, assign curves to pumps,
or write model files. A caller must review nominal flow, receiving head,
representative wetwell water level, force-main path and margin assumptions.
Manufacturer curves and measured capacity remain separate evidence.

Full circular-pipe Hazen-Williams friction follows the
[EPA5.2.4 force-main implementation](https://raw.githubusercontent.com/USEPA/Stormwater-Management-Model/v5.2.4/src/solver/forcmain.c).
The SI interface converts internally to the engine's US relation:
hf = L*(v/(1.318*C))^1.852/R^1.1667, where R=D/4 and v=Q/area.
Lengths/diameters and returned head are metres, flow is cubic metres per second,
and C is an explicitly supplied dimensionless coefficient. This assumes a full
pipe and turbulent water flow; it excludes minor losses, air and transients.

For each proposed curve, Hn=max(0,static lift+friction)+explicit margin.
The increasing-head native axes are (0,Qn*runout factor), (Hn,Qn),
(Hn*shutoff factor,0). Default shape factors1.25 and1.3 are visible assumptions;
there is no hidden default margin. Parallel pumps must use combined simultaneous
flow in every shared segment's friction calculation. A downhill nominal system
head is flagged rather than hidden by altering source elevations.

Review each curve's static anchor and intermediate crest/air-valve conditions.
These helpers do not prove pump capacity, pressure rating, NPSH, priming,
transient protection or calibration. Compare actual written head/flow axes and
all retained original records in downstream manual QA diffs.

## Developer README

Import `hazen_williams_full_pipe_headloss` and `provisional_pump3_points` from
`umdev.pump_curves`. Inputs are finite real numbers; invalid units must be caught
by orchestration, and nonpositive dimensions/C, negative flow, invalid shape
factors or nonfinite results are rejected. Returned structured points remain SI;
SHMM performs the eventual explicitly configured model-writing operation.

Run `python -m pytest tests/test_pump_curves.py -q`. Checks include a1000ft/1ft/
1cfs/C120 hand calculation, zero flow, dimensional flow/length scaling, diameter
monotonicity, combined-flow friction, native axis order and invalid inputs.
