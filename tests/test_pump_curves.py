"""Hand, dimensional and invalid-input checks for provisional pump preparation.

Author: Lucas HT Nguyen; Contact: https://github.com/lucashtnguyen
Date: 2026-10-16.
"""
import math
import pytest
from umdev.pump_curves import hazen_williams_full_pipe_headloss,provisional_pump3_points


def test_full_pipe_hand_check_us_to_si():
    #1000ft long,1ftdiameter,1cfs,C120: v=4/pi,R=.25ft.
    expected_ft=1000*(4/math.pi/(1.318*120))**1.852/.25**1.1667
    assert .66<expected_ft<.67
    actual=hazen_williams_full_pipe_headloss(304.8,.3048,.3048**3,120.)
    assert actual==pytest.approx(expected_ft*.3048,rel=1e-12)


def test_flow_scaling_length_and_zero():
    base=hazen_williams_full_pipe_headloss(100.,.3,.02,120.)
    assert hazen_williams_full_pipe_headloss(200.,.3,.02,120.)==pytest.approx(2*base)
    assert hazen_williams_full_pipe_headloss(100.,.3,.04,120.)==pytest.approx(base*2**1.852)
    assert hazen_williams_full_pipe_headloss(100.,.6,.02,120.)<base
    assert hazen_williams_full_pipe_headloss(100.,.3,0.,120.)==0.


@pytest.mark.parametrize('args',[(0.,.3,.02,120.),(100.,-1.,.02,120.),(100.,.3,-1.,120.),(100.,.3,.02,0.),(True,.3,.02,120.),(math.inf,.3,.02,120.)])
def test_invalid_friction_input(args):
    with pytest.raises(ValueError):hazen_williams_full_pipe_headloss(*args)


def test_pump3_axes_shared_friction_and_downhill_case():
    r=provisional_pump3_points(.01,20.,10.,5.)
    assert r['points']==[{'head_m':0.,'flow_m3_s':.0125},{'head_m':35.,'flow_m3_s':.01},{'head_m':45.5,'flow_m3_s':0.}]
    assert r['axes']=={'x':'head_m','y':'flow_m3_s'}
    assert provisional_pump3_points(.01,-20.,5.,3.)['duty_head_m']==3.
    shared=hazen_williams_full_pipe_headloss(100.,.2,.02,120.)
    solo=hazen_williams_full_pipe_headloss(100.,.2,.01,120.)
    assert shared/solo==pytest.approx(2**1.852)


@pytest.mark.parametrize('kwargs',[{'nominal_flow_m3_s':0.},{'friction_head_m':-1.},{'duty_margin_m':0.},{'shutoff_head_factor':1.},{'runout_flow_factor':True}])
def test_invalid_curve_input(kwargs):
    args=dict(nominal_flow_m3_s=.01,static_lift_m=20.,friction_head_m=10.,duty_margin_m=5.);args.update(kwargs)
    with pytest.raises(ValueError):provisional_pump3_points(**args)


@pytest.mark.parametrize('diameter',[1e-300,1e300])
def test_unrepresentable_floating_geometry_fails_clearly(diameter):
    with pytest.raises(ValueError,match='calculation range'):
        hazen_williams_full_pipe_headloss(100.,diameter,.02,120.)
