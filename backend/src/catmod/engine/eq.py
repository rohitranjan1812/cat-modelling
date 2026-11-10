"""Earthquake hazard: stochastic catalogue + ground-motion model (PGA, g).

Catalogue
---------
Sources are faults (polyline traces) or area zones. Magnitudes follow a doubly-truncated
Gutenberg-Richter law; the catalogue is *enumerated* (magnitude bins x rupture positions), not
sampled, so event rates are exact and the set is identical on every machine:

    P(bin) = (10^{-b(m_lo - Mmin)} - 10^{-b(m_hi - Mmin)}) / (1 - 10^{-b(Mmax - Mmin)})
    rate(event) = rate(M >= Mmin) * P(bin) / n_positions

Rupture length uses Wells & Coppersmith (1994), log10 L = -3.22 + 0.69 M (km); ruptures slide
along the trace in ``position_step_km`` steps. Area zones place ruptures on a deterministic
sunflower lattice with golden-angle strikes.

Ground motion
-------------
    ln PGA = a + b (M' - 6) + c (M' - 6)^2 - ln(sqrt(R^2 + h^2)) - d sqrt(R^2 + h^2)
             + s ln(Vs30 / 760),        M' = min(M, m_sat),  R = sqrt(R_jb^2 + z_tor^2)

with between-event sigma tau and within-event sigma phi. Coefficients are a compact functional
form calibrated to typical NGA-style medians (M6 @10 km ~ 0.2 g, M7 @10 km ~ 0.35 g, rock) — they
are illustrative, not a licensed GMM; swap them via config or a plugin hazard.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Any, ClassVar, Literal

import numpy as np
from pydantic import Field

from catmod.config import StrictModel
from catmod.engine.context import Exposure, local_xy_km
from catmod.engine.hazard import F64, Event, Footprint, empty_footprint
from catmod.geo import haversine_km

GOLDEN_ANGLE = np.pi * (3.0 - np.sqrt(5.0))


class EqSource(StrictModel):
    name: str
    kind: Literal["fault", "area"]
    trace: list[tuple[float, float]] = Field(
        default_factory=list, description="Fault trace (lat, lon) vertices"
    )
    center: tuple[float, float] | None = Field(default=None, description="Area zone (lat, lon)")
    radius_km: float = Field(default=60.0, gt=0)
    m_min: float = Field(ge=4.0, le=9.5)
    m_max: float = Field(ge=4.0, le=9.6)
    rate: float = Field(gt=0, description="Annual rate of M >= m_min")
    b_value: float = Field(default=0.9, gt=0.3, lt=2.0)
    ztor_km: float = Field(default=0.0, ge=0, le=80, description="Depth to top of rupture")


class GmmCoefficients(StrictModel):
    a: float = 0.893
    b: float = 0.715
    c: float = -0.155
    h: float = 6.0
    d: float = 0.004
    s: float = -0.5
    m_sat: float = 8.3


class FieldConfig(StrictModel):
    model: str = "fft"
    range_km: float = Field(default=20.0, gt=0)
    grid_km: float | None = Field(default=None, gt=0)


def _fault(
    name: str,
    trace: list[tuple[float, float]],
    m_min: float,
    m_max: float,
    rate: float,
    ztor: float = 0.0,
) -> EqSource:
    return EqSource(
        name=name, kind="fault", trace=trace, m_min=m_min, m_max=m_max, rate=rate, ztor_km=ztor
    )


def _area(
    name: str,
    center: tuple[float, float],
    radius: float,
    m_min: float,
    m_max: float,
    rate: float,
    ztor: float = 5.0,
) -> EqSource:
    return EqSource(
        name=name,
        kind="area",
        center=center,
        radius_km=radius,
        m_min=m_min,
        m_max=m_max,
        rate=rate,
        ztor_km=ztor,
    )


# Illustrative West-Coast source model (simplified traces; rates indicative only).
DEFAULT_SOURCES: list[EqSource] = [
    _fault(
        "San Andreas (south)",
        [(33.35, -115.70), (34.00, -116.80), (34.35, -117.60), (34.80, -118.90), (35.70, -120.30)],
        6.0,
        8.0,
        0.040,
    ),
    _fault(
        "San Andreas (north)",
        [(36.80, -121.50), (37.50, -122.40), (38.30, -123.10), (39.00, -123.70)],
        6.0,
        7.9,
        0.020,
    ),
    _fault(
        "Hayward-Rodgers Creek",
        [(37.40, -121.90), (37.80, -122.20), (38.20, -122.60)],
        6.0,
        7.3,
        0.015,
    ),
    _fault("San Jacinto", [(32.80, -115.60), (33.50, -116.50), (34.10, -117.30)], 6.0, 7.5, 0.020),
    _fault("Newport-Inglewood", [(33.60, -117.90), (34.00, -118.40)], 5.5, 7.2, 0.010),
    _fault("Puente Hills thrust", [(33.95, -118.00), (34.05, -118.35)], 5.5, 7.1, 0.008, 3.0),
    _fault("Rose Canyon", [(32.60, -117.20), (33.00, -117.30)], 5.5, 7.0, 0.003),
    _fault("Seattle fault", [(47.55, -122.90), (47.60, -122.00)], 6.0, 7.4, 0.002),
    _fault(
        "Cascadia subduction",
        [(40.50, -124.60), (43.00, -124.80), (46.00, -124.60), (48.30, -125.20)],
        8.0,
        9.1,
        0.002,
        10.0,
    ),
    _area("LA basin background", (34.05, -118.25), 80.0, 5.0, 6.5, 0.30),
    _area("SF Bay background", (37.77, -122.42), 70.0, 5.0, 6.5, 0.15),
    _area("San Diego background", (32.72, -117.16), 60.0, 5.0, 6.5, 0.05),
    _area("Puget Sound intraslab", (47.40, -122.50), 80.0, 5.0, 7.0, 0.05, 45.0),
]


class EqConfig(StrictModel):
    sources: list[EqSource] = Field(default_factory=lambda: list(DEFAULT_SOURCES))
    dm: float = Field(default=0.2, gt=0.01, le=1.0, description="Magnitude bin width")
    position_step_km: float = Field(default=15.0, gt=1)
    area_sites: int = Field(default=12, ge=1, le=500)
    tau: float = Field(default=0.35, ge=0)
    phi: float = Field(default=0.55, ge=0)
    gmm: GmmCoefficients = GmmCoefficients()
    max_distance_km: float = Field(default=300.0, gt=0)
    min_median_pga: float = Field(default=0.01, ge=0, description="Prune sites below this (g)")
    field: FieldConfig = FieldConfig()


@dataclass(frozen=True)
class EqEvent(Event):
    mag: float
    lat1: float
    lon1: float
    lat2: float
    lon2: float
    ztor_km: float
    source: str

    @property
    def mid(self) -> tuple[float, float]:
        return (self.lat1 + self.lat2) / 2.0, (self.lon1 + self.lon2) / 2.0

    def summary(self) -> dict[str, Any]:
        lat, lon = self.mid
        return {
            "lat": lat,
            "lon": lon,
            "magnitude": self.mag,
            "vmax_ms": None,
            "depth_km": self.ztor_km,
            "source": self.source,
        }

    def geometry(self) -> dict[str, Any]:
        return {
            "type": "LineString",
            "coordinates": [[self.lon1, self.lat1], [self.lon2, self.lat2]],
        }


def rupture_length_km(m: float) -> float:
    return float(10.0 ** (-3.22 + 0.69 * m))


def _trace_point(lat: F64, lon: F64, cum: F64, s: float) -> tuple[float, float]:
    """Point at arc-length s along a polyline (linear interpolation in lat/lon per segment)."""
    s = float(np.clip(s, 0.0, cum[-1]))
    i = int(np.clip(np.searchsorted(cum, s, side="right") - 1, 0, len(cum) - 2))
    seg = cum[i + 1] - cum[i]
    t = 0.0 if seg == 0 else (s - cum[i]) / seg
    return float(lat[i] + t * (lat[i + 1] - lat[i])), float(lon[i] + t * (lon[i + 1] - lon[i]))


def _gr_bins(src: EqSource, dm: float) -> list[tuple[float, float]]:
    """(bin centre magnitude, probability) for a truncated GR distribution."""
    edges = np.arange(src.m_min, src.m_max + 1e-9, dm)
    if edges[-1] < src.m_max - 1e-9:
        edges = np.append(edges, src.m_max)
    if edges.size < 2:
        edges = np.array([src.m_min, src.m_max + dm])
    b, m0, m1 = src.b_value, src.m_min, edges[-1]
    norm = 1.0 - 10.0 ** (-b * (m1 - m0))
    out = []
    for lo, hi in pairwise(edges):
        p = (10.0 ** (-b * (lo - m0)) - 10.0 ** (-b * (hi - m0))) / norm
        out.append(((lo + hi) / 2.0, float(p)))
    return out


def point_segment_km(lat: F64, lon: F64, ev: EqEvent) -> F64:
    """Horizontal distance (Joyner-Boore-like) from sites to the rupture segment."""
    lat0, lon0 = ev.mid
    px, py = local_xy_km(lat, lon, lat0, lon0)
    ax, ay = local_xy_km(np.array([ev.lat1]), np.array([ev.lon1]), lat0, lon0)
    bx, by = local_xy_km(np.array([ev.lat2]), np.array([ev.lon2]), lat0, lon0)
    vx, vy = float(bx[0] - ax[0]), float(by[0] - ay[0])
    vv = vx * vx + vy * vy
    t = (
        np.zeros_like(px)
        if vv == 0
        else np.clip(((px - ax[0]) * vx + (py - ay[0]) * vy) / vv, 0.0, 1.0)
    )
    out: F64 = np.hypot(px - (ax[0] + t * vx), py - (ay[0] + t * vy))
    return out


class EarthquakeHazard:
    peril: ClassVar[str] = "QEQ"
    unit: ClassVar[str] = "PGA (g)"
    ID_BASE: ClassVar[int] = 1_000_000

    def __init__(self, config: EqConfig | None = None) -> None:
        self.cfg = config or EqConfig()

    def field_params(self) -> dict[str, Any]:
        return self.cfg.field.model_dump(exclude_none=True)

    def ln_pga(self, mag: float, r_km: F64, vs30: F64) -> F64:
        g = self.cfg.gmm
        m = min(mag, g.m_sat) - 6.0
        rr = np.sqrt(r_km**2 + g.h**2)
        out: F64 = g.a + g.b * m + g.c * m * m - np.log(rr) - g.d * rr + g.s * np.log(vs30 / 760.0)
        return out

    # ------------------------------------------------------------------ catalogue
    def catalog(self) -> list[Event]:
        events: list[Event] = []
        eid = self.ID_BASE
        for src in self.cfg.sources:
            for m, p in _gr_bins(src, self.cfg.dm):
                ruptures = self._ruptures(src, m)
                for k, (la1, lo1, la2, lo2) in enumerate(ruptures):
                    eid += 1
                    events.append(
                        EqEvent(
                            event_id=eid,
                            peril=self.peril,
                            rate=src.rate * p / len(ruptures),
                            name=f"{src.name} M{m:.1f} #{k + 1}",
                            mag=round(m, 2),
                            lat1=la1,
                            lon1=lo1,
                            lat2=la2,
                            lon2=lo2,
                            ztor_km=src.ztor_km,
                            source=src.name,
                        )
                    )
        return events

    def _ruptures(self, src: EqSource, m: float) -> list[tuple[float, float, float, float]]:
        length = rupture_length_km(m)
        if src.kind == "fault":
            lat = np.array([p[0] for p in src.trace])
            lon = np.array([p[1] for p in src.trace])
            seg = haversine_km(lat[:-1], lon[:-1], lat[1:], lon[1:])
            cum = np.concatenate([[0.0], np.cumsum(seg)])
            total = float(cum[-1])
            length = min(length, total)
            n_pos = max(1, int((total - length) // self.cfg.position_step_km) + 1)
            starts = np.linspace(0.0, total - length, n_pos)
            return [
                (*_trace_point(lat, lon, cum, s), *_trace_point(lat, lon, cum, s + length))
                for s in starts
            ]
        assert src.center is not None
        from catmod.geo import destination

        n = self.cfg.area_sites
        k = np.arange(n)
        r = src.radius_km * np.sqrt((k + 0.5) / n)
        theta = k * GOLDEN_ANGLE
        clat = np.full(n, src.center[0])
        clon = np.full(n, src.center[1])
        mlat, mlon = destination(clat, clon, theta, r)
        strike = (k * GOLDEN_ANGLE * 0.5) % np.pi
        half = np.full(n, length / 2.0)
        alat, alon = destination(mlat, mlon, strike, half)
        blat, blon = destination(mlat, mlon, strike + np.pi, half)
        return [
            (float(a), float(b), float(c), float(d))
            for a, b, c, d in zip(alat, alon, blat, blon, strict=True)
        ]

    # ------------------------------------------------------------------ footprint
    def footprint(self, event: Event, exposure: Exposure) -> Footprint:
        assert isinstance(event, EqEvent)
        cfg = self.cfg
        if event.mag < 7:
            radius = min(cfg.max_distance_km, 150.0)
        elif event.mag < 8:
            radius = min(cfg.max_distance_km, 250.0)
        else:
            radius = cfg.max_distance_km
        n_probe = max(
            2,
            int(
                haversine_km(
                    np.array([event.lat1]),
                    np.array([event.lon1]),
                    np.array([event.lat2]),
                    np.array([event.lon2]),
                )[0]
                // 10
            )
            + 2,
        )
        t = np.linspace(0.0, 1.0, n_probe)
        probe_lat = event.lat1 + t * (event.lat2 - event.lat1)
        probe_lon = event.lon1 + t * (event.lon2 - event.lon1)
        idx = exposure.within(probe_lat, probe_lon, radius)
        idx = idx[exposure.perils[self.peril][idx]]
        if idx.size == 0:
            return empty_footprint(cfg.tau, cfg.phi)
        rjb = point_segment_km(exposure.lat[idx], exposure.lon[idx], event)
        r = np.sqrt(rjb**2 + event.ztor_km**2)
        ln_med = self.ln_pga(event.mag, r, exposure.vs30[idx])
        keep = ln_med >= np.log(max(cfg.min_median_pga, 1e-12))
        idx, ln_med = idx[keep], ln_med[keep]
        lat0, lon0 = event.mid
        x, y = local_xy_km(exposure.lat[idx], exposure.lon[idx], lat0, lon0)
        return Footprint(idx, ln_med, cfg.tau, cfg.phi, x, y)

    # ------------------------------------------------------------------ scenarios / live
    def event_from_spec(self, spec: dict[str, Any], event_id: int) -> Event:
        """Point-source spec {lat, lon, magnitude, depth_km?, strike_deg?, rate?, name?}."""
        from catmod.geo import destination

        mag = float(spec["magnitude"])
        lat, lon = float(spec["lat"]), float(spec["lon"])
        strike = np.radians(float(spec.get("strike_deg", 0.0)))
        half = np.array([rupture_length_km(mag) / 2.0])
        a = destination(np.array([lat]), np.array([lon]), np.array([strike]), half)
        b = destination(np.array([lat]), np.array([lon]), np.array([strike + np.pi]), half)
        depth = float(spec.get("depth_km", 10.0))
        return EqEvent(
            event_id=event_id,
            peril=self.peril,
            rate=float(spec.get("rate", 0.0)),
            name=str(spec.get("name", f"M{mag:.1f} scenario")),
            mag=mag,
            lat1=float(a[0][0]),
            lon1=float(a[1][0]),
            lat2=float(b[0][0]),
            lon2=float(b[1][0]),
            ztor_km=max(0.0, depth - 5.0),
            source=str(spec.get("source", "scenario")),
        )
