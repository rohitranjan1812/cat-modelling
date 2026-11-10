"""Hazard catalogues, ground motion, wind field and spatial index."""

from __future__ import annotations

import numpy as np
import pytest

from catmod.engine.context import Exposure
from catmod.engine.eq import EarthquakeHazard, EqConfig, rupture_length_km
from catmod.engine.tc import CycloneHazard, TcConfig, TcEvent, _bearing, willoughby_rmax
from catmod.geo import haversine_km


def test_eq_catalog_rates_sum_to_source_rates() -> None:
    cfg = EqConfig(dm=0.5, position_step_km=60, area_sites=3)
    cat = EarthquakeHazard(cfg).catalog()
    total = sum(e.rate for e in cat)
    assert total == pytest.approx(sum(s.rate for s in cfg.sources), rel=1e-9)
    ids = [e.event_id for e in cat]
    assert len(ids) == len(set(ids))


def test_gmm_monotone_and_calibrated() -> None:
    hz = EarthquakeHazard()
    r = np.array([1.0, 10.0, 50.0, 150.0])
    rock = np.full(4, 760.0)
    m6, m7 = np.exp(hz.ln_pga(6.0, r, rock)), np.exp(hz.ln_pga(7.0, r, rock))
    assert np.all(np.diff(m6) < 0) and np.all(m7 > m6)
    assert m6[1] == pytest.approx(0.2, rel=0.05)
    assert m7[1] == pytest.approx(0.35, rel=0.05)
    soft = np.exp(hz.ln_pga(6.0, r, np.full(4, 300.0)))
    assert np.all(soft > m6)


def test_wells_coppersmith() -> None:
    assert rupture_length_km(7.0) == pytest.approx(10 ** (-3.22 + 4.83), rel=1e-12)


def test_tc_catalog_rate_matches_climatology() -> None:
    cfg = TcConfig(gate_spacing_km=300, vmax_step=15)
    cat = CycloneHazard(cfg).catalog()
    assert sum(e.rate for e in cat) == pytest.approx(cfg.annual_landfall_rate, rel=1e-9)


def test_holland_profile_peaks_at_rmax() -> None:
    hz = CycloneHazard(TcConfig(max_radius_km=500))
    ev = hz.event_from_spec(
        {
            "lat": 27.0,
            "lon": -80.0,
            "vmax_ms": 50.0,
            "rmax_km": 30.0,
            "translation_ms": 1e-6,
            "heading_deg": 0.0,
        },
        1,
    )
    assert isinstance(ev, TcEvent)
    # sites due west (left of a northward track) to avoid the asymmetry term
    from catmod.geo import destination

    d = np.array([10.0, 30.0, 60.0, 150.0])
    lat, lon = destination(np.full(4, 27.0), np.full(4, -80.0), np.full(4, -np.pi / 2), d)
    w = hz.wind_at(ev, lat, lon)
    assert w[1] == pytest.approx(50.0, rel=0.02)
    assert w[1] > w[0] and w[1] > w[2] > w[3]


def test_kaplan_demaria_decay() -> None:
    hz = CycloneHazard()
    v = hz.decay(60.0, np.array([0.0, 12.0, 48.0]))
    assert v[0] == pytest.approx(0.9 * 60.0)
    assert v[2] < v[1] < v[0]
    assert v[2] > hz.cfg.decay_vb


def test_wind_field_matches_great_circle_bruteforce(exposure: Exposure) -> None:
    hz = CycloneHazard()
    ev = hz.event_from_spec({"lat": 25.8, "lon": -80.2, "vmax_ms": 65.0, "heading_deg": 300}, 7)
    assert isinstance(ev, TcEvent)
    fp = hz.footprint(ev, exposure)
    assert fp.idx.size > 50
    assert np.unique(fp.idx).size == fp.idx.size
    lat, lon = exposure.lat[fp.idx], exposure.lon[fp.idx]
    best = np.zeros(lat.size)
    for k in range(len(ev.t_h)):
        r = np.maximum(
            haversine_km(np.full(lat.size, ev.lat[k]), np.full(lat.size, ev.lon[k]), lat, lon), 1e-3
        )
        x = (ev.rmax[k] / r) ** hz.cfg.holland_b
        v = ev.vmax[k] * np.sqrt(x * np.exp(1 - x))
        b = _bearing(np.full(lat.size, ev.lat[k]), np.full(lat.size, ev.lon[k]), lat, lon)
        v += 0.5 * ev.vt[k] * np.cos(b - (ev.heading[k] + np.pi / 2))
        v[r > hz.cfg.max_radius_km] = 0
        best = np.maximum(best, v)
    assert np.allclose(np.exp(fp.ln_median), best, rtol=0.015)


def test_landfall_detection_on_custom_track() -> None:
    hz = CycloneHazard()
    track = [
        {"lat": 24.0, "lon": -78.0, "vmax_ms": 60.0},
        {"lat": 27.0, "lon": -82.0, "vmax_ms": 60.0},
    ]
    ev = hz.track_event(track, 9, "test")
    assert ev.landfall is not None
    assert 25.0 < ev.landfall[0] < 27.0
    # intensity decays after landfall
    assert ev.vmax[-1] < 60.0
    assert willoughby_rmax(70, 25) < willoughby_rmax(35, 25)


def test_within_is_exact_and_unique(exposure: Exposure) -> None:
    probes_lat, probes_lon = np.array([34.0, 29.8]), np.array([-118.2, -95.4])
    got = exposure.within(probes_lat, probes_lon, 40.0)
    d = np.minimum(
        haversine_km(
            np.full(exposure.n, 34.0), np.full(exposure.n, -118.2), exposure.lat, exposure.lon
        ),
        haversine_km(
            np.full(exposure.n, 29.8), np.full(exposure.n, -95.4), exposure.lat, exposure.lon
        ),
    )
    assert np.array_equal(got, np.flatnonzero(d <= 40.0))
