"""Tropical-cyclone wind hazard (1-min sustained surface wind, m/s).

Catalogue
---------
Enumerated landfall set on a coastal gate (Texas -> North Carolina, ordered so land is on the
LEFT of the gate direction). Each event = (gate position, intensity bin, heading offset, Rmax
quantile). Landfall rates distribute ``annual_landfall_rate`` along the gate by a piecewise
density, then by an exponential intensity law P(V) ~ exp(-(V - Vmin)/scale).

Wind field
----------
Holland (1980) gradient profile in normalised form (exact at r = Rmax):

    V(r) = Vmax(t) * sqrt( (Rmax/r)^B * exp(1 - (Rmax/r)^B) )

plus forward-motion asymmetry 0.5 * Vt * cos(theta - (heading + 90 deg)) (strongest right of
track, Northern Hemisphere). After landfall intensity decays per Kaplan & DeMaria (1995):

    Vmax(t) = Vb + (R * V0 - Vb) * exp(-alpha * t),   Vb = 13.7 m/s, R = 0.9, alpha = 0.095/h

Rmax median follows Willoughby et al. (2006): Rmax = 46.4 exp(-0.0155 Vmax + 0.0169 lat) km.
Site intensity is the maximum over hourly track positions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np
from pydantic import Field

from catmod.config import StrictModel
from catmod.engine.context import Exposure, local_xy_km
from catmod.engine.eq import FieldConfig
from catmod.engine.hazard import F64, Event, Footprint, empty_footprint
from catmod.geo import destination, haversine_km

KT_TO_MS = 0.514444
MPH_TO_MS = 0.44704

# Coastal gate: Texas -> North Carolina (lat, lon). Land lies to the left of travel.
DEFAULT_GATE: list[tuple[float, float]] = [
    (25.95, -97.15),
    (27.80, -97.10),
    (29.30, -94.80),
    (29.75, -93.30),
    (29.25, -90.70),
    (30.30, -89.10),
    (30.35, -87.20),
    (29.70, -85.00),
    (29.10, -83.00),
    (27.70, -82.70),
    (26.10, -81.80),
    (25.15, -80.95),
    (25.80, -80.13),
    (26.70, -80.03),
    (28.40, -80.60),
    (30.30, -81.40),
    (32.00, -80.85),
    (32.75, -79.90),
    (33.70, -78.85),
    (34.20, -77.85),
    (35.20, -75.55),
]

# Relative landfall density at each gate vertex (interpolated along the gate).
DEFAULT_GATE_DENSITY: list[float] = [
    1.0,
    1.0,
    1.1,
    1.0,
    1.2,
    1.1,
    1.2,
    1.1,
    0.8,
    1.0,
    1.3,
    1.5,
    1.5,
    1.2,
    0.9,
    0.5,
    0.4,
    0.8,
    0.9,
    1.0,
    1.1,
]


class TcConfig(StrictModel):
    gate: list[tuple[float, float]] = Field(default_factory=lambda: list(DEFAULT_GATE))
    gate_density: list[float] = Field(default_factory=lambda: list(DEFAULT_GATE_DENSITY))
    annual_landfall_rate: float = Field(default=1.7, gt=0, description="Hurricanes / year")
    gate_spacing_km: float = Field(default=100.0, gt=5)
    vmax_min: float = Field(default=33.0, gt=10, description="m/s at landfall (Cat 1 ~ 33)")
    vmax_max: float = Field(default=80.0, gt=10)
    vmax_step: float = Field(default=5.0, gt=0.5)
    vmax_scale: float = Field(default=14.0, gt=0)
    heading_offsets_deg: list[float] = Field(default_factory=lambda: [-30.0, 0.0, 30.0])
    heading_weights: list[float] = Field(default_factory=lambda: [0.25, 0.5, 0.25])
    rmax_factors: list[float] = Field(default_factory=lambda: [0.7, 1.4])
    rmax_weights: list[float] = Field(default_factory=lambda: [0.5, 0.5])
    holland_b: float = Field(default=1.4, ge=0.8, le=2.5)
    translation_ms: float = Field(default=6.0, gt=0)
    decay_alpha: float = Field(default=0.095, gt=0)
    decay_vb: float = Field(default=13.7, ge=0)
    decay_r: float = Field(default=0.9, gt=0, le=1)
    hours_before: int = Field(default=24, ge=0)
    hours_after: int = Field(default=36, ge=1)
    max_radius_km: float = Field(default=400.0, gt=0)
    min_median_wind: float = Field(default=20.0, ge=0)
    tau: float = Field(default=0.08, ge=0)
    phi: float = Field(default=0.12, ge=0)
    field: FieldConfig = FieldConfig(range_km=60.0)


def willoughby_rmax(vmax: float, lat: float) -> float:
    return float(46.4 * np.exp(-0.0155 * vmax + 0.0169 * abs(lat)))


def _bearing(lat1: F64, lon1: F64, lat2: F64, lon2: F64) -> F64:
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dl = np.radians(lon2 - lon1)
    out: F64 = np.arctan2(
        np.sin(dl) * np.cos(p2), np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl)
    )
    return out


@dataclass(frozen=True)
class TcEvent(Event):
    t_h: tuple[float, ...]
    lat: tuple[float, ...]
    lon: tuple[float, ...]
    vmax: tuple[float, ...]  # m/s, decay already applied
    rmax: tuple[float, ...]  # km
    heading: tuple[float, ...]  # radians, direction of motion
    vt: tuple[float, ...]  # translation speed m/s
    landfall: tuple[float, float] | None
    v_landfall: float

    def summary(self) -> dict[str, Any]:
        lat, lon = self.landfall if self.landfall else (self.lat[0], self.lon[0])
        i = int(np.argmin(np.abs(np.asarray(self.t_h))))
        return {
            "lat": lat,
            "lon": lon,
            "magnitude": None,
            "vmax_ms": self.v_landfall,
            "depth_km": None,
            "source": (
                f"Rmax {self.rmax[i]:.0f} km, heading {np.degrees(self.heading[i]) % 360:.0f}°"
            ),
        }

    def geometry(self) -> dict[str, Any]:
        return {
            "type": "LineString",
            "coordinates": [[lo, la] for la, lo in zip(self.lat, self.lon, strict=True)],
        }


def _seg_intersection(p1: F64, p2: F64, q1: F64, q2: F64) -> float | None:
    """Parameter s in [0,1] along p1->p2 where it crosses q1->q2 (planar lon/lat), else None."""
    r, s = p2 - p1, q2 - q1
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-12:
        return None
    qp = q1 - p1
    t = (qp[0] * s[1] - qp[1] * s[0]) / den
    u = (qp[0] * r[1] - qp[1] * r[0]) / den
    return float(t) if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0 else None


class CycloneHazard:
    peril: ClassVar[str] = "WTC"
    unit: ClassVar[str] = "Wind (m/s)"
    ID_BASE: ClassVar[int] = 2_000_000

    def __init__(self, config: TcConfig | None = None) -> None:
        self.cfg = config or TcConfig()

    def field_params(self) -> dict[str, Any]:
        return self.cfg.field.model_dump(exclude_none=True)

    # ------------------------------------------------------------------ geometry helpers
    def _gate_arrays(self) -> tuple[F64, F64, F64]:
        lat = np.array([p[0] for p in self.cfg.gate])
        lon = np.array([p[1] for p in self.cfg.gate])
        cum = np.concatenate([[0.0], np.cumsum(haversine_km(lat[:-1], lon[:-1], lat[1:], lon[1:]))])
        return lat, lon, cum

    def decay(self, v0: float, t_after: F64) -> F64:
        c = self.cfg
        out: F64 = c.decay_vb + (c.decay_r * v0 - c.decay_vb) * np.exp(-c.decay_alpha * t_after)
        return np.minimum(out, v0)

    def straight_track(
        self,
        lat0: float,
        lon0: float,
        heading: float,
        vt: float,
        v0: float,
        rmax: float,
        t_landfall: float | None = 0.0,
    ) -> dict[str, F64]:
        c = self.cfg
        t = np.arange(-c.hours_before, c.hours_after + 1, 1.0)
        dist = vt * 3.6 * t
        lat, lon = destination(
            np.full(t.size, lat0),
            np.full(t.size, lon0),
            np.where(dist >= 0, heading, heading + np.pi),
            np.abs(dist),
        )
        vmax = np.full(t.size, v0)
        if t_landfall is not None:
            after = t > t_landfall
            vmax[after] = self.decay(v0, t[after] - t_landfall)
        return {
            "t": t,
            "lat": lat,
            "lon": lon,
            "vmax": vmax,
            "rmax": np.full(t.size, rmax),
            "heading": np.full(t.size, heading),
            "vt": np.full(t.size, vt),
        }

    # ------------------------------------------------------------------ catalogue
    def catalog(self) -> list[Event]:
        c = self.cfg
        glat, glon, cum = self._gate_arrays()
        dens = np.asarray(c.gate_density)
        if dens.size != glat.size:
            raise ValueError("gate_density must have one value per gate vertex")
        total = float(cum[-1])
        n_pos = max(1, int(total // c.gate_spacing_km))
        s_pos = (np.arange(n_pos) + 0.5) * total / n_pos
        w_pos = np.interp(s_pos, cum, dens)
        w_pos = w_pos / w_pos.sum()

        v_edges = np.arange(c.vmax_min, c.vmax_max + 1e-9, c.vmax_step)
        v_mid = (v_edges[:-1] + v_edges[1:]) / 2.0
        p_v = np.exp(-(v_edges[:-1] - c.vmax_min) / c.vmax_scale) - np.exp(
            -(v_edges[1:] - c.vmax_min) / c.vmax_scale
        )
        p_v = p_v / p_v.sum()
        hw = np.asarray(c.heading_weights) / np.sum(c.heading_weights)
        rw = np.asarray(c.rmax_weights) / np.sum(c.rmax_weights)

        events: list[Event] = []
        eid = self.ID_BASE
        for i, s in enumerate(s_pos):
            j = int(np.clip(np.searchsorted(cum, s, side="right") - 1, 0, glat.size - 2))
            frac = (s - cum[j]) / max(cum[j + 1] - cum[j], 1e-9)
            plat = glat[j] + frac * (glat[j + 1] - glat[j])
            plon = glon[j] + frac * (glon[j + 1] - glon[j])
            tangent = float(
                _bearing(
                    glat[j : j + 1], glon[j : j + 1], glat[j + 1 : j + 2], glon[j + 1 : j + 2]
                )[0]
            )
            inland = tangent - np.pi / 2.0  # land is on the left of the gate direction
            for vi, v in enumerate(v_mid):
                for hi, off in enumerate(c.heading_offsets_deg):
                    heading = inland + np.radians(off)
                    for ri, rf in enumerate(c.rmax_factors):
                        eid += 1
                        rmax = willoughby_rmax(float(v), float(plat)) * rf
                        tr = self.straight_track(
                            float(plat),
                            float(plon),
                            float(heading),
                            c.translation_ms,
                            float(v),
                            rmax,
                        )
                        rate = c.annual_landfall_rate * w_pos[i] * p_v[vi] * hw[hi] * rw[ri]
                        events.append(
                            self._make(
                                eid,
                                float(rate),
                                f"Landfall {plat:.2f},{plon:.2f} V{v:.0f} h{off:+.0f} R{rf:g}",
                                tr,
                                (float(plat), float(plon)),
                                float(v),
                            )
                        )
        return events

    def _make(
        self,
        eid: int,
        rate: float,
        name: str,
        tr: dict[str, F64],
        landfall: tuple[float, float] | None,
        v_land: float,
    ) -> TcEvent:
        def tup(a: F64) -> tuple[float, ...]:
            return tuple(float(x) for x in a)

        return TcEvent(
            event_id=eid,
            peril=self.peril,
            rate=rate,
            name=name,
            t_h=tup(tr["t"]),
            lat=tup(tr["lat"]),
            lon=tup(tr["lon"]),
            vmax=tup(tr["vmax"]),
            rmax=tup(tr["rmax"]),
            heading=tup(tr["heading"]),
            vt=tup(tr["vt"]),
            landfall=landfall,
            v_landfall=v_land,
        )

    # ------------------------------------------------------------------ footprint
    def wind_at(self, ev: TcEvent, lat: F64, lon: F64) -> F64:
        """Max wind (m/s) over the track at each site, vectorised over (time, site).

        Uses a local equirectangular projection about the site centroid: over the <= 400 km
        radius of influence its distance error is < 1%, far below wind-field uncertainty.
        """
        c = self.cfg
        if lat.size == 0:
            return np.zeros(0)
        lat0, lon0 = float(lat.mean()), float(lon.mean())
        sx, sy = local_xy_km(lat, lon, lat0, lon0)
        tx, ty = local_xy_km(np.asarray(ev.lat), np.asarray(ev.lon), lat0, lon0)
        # keep only time steps whose centre comes within the influence radius of the sites
        reach = c.max_radius_km + float(np.hypot(sx - sx.mean(), sy - sy.mean()).max())
        steps = np.flatnonzero(np.hypot(tx - sx.mean(), ty - sy.mean()) <= reach)
        if steps.size == 0:
            return np.zeros(lat.size)
        tx, ty = tx[steps], ty[steps]
        vmax, rmax = np.asarray(ev.vmax)[steps], np.asarray(ev.rmax)[steps]
        head, vt = np.asarray(ev.heading)[steps], np.asarray(ev.vt)[steps]
        # Evaluate every in-reach hourly position: with inland decay the peak at a site can
        # occur hours before closest approach (stronger storm, larger distance), so a
        # closest-approach shortcut under-estimates inland winds by up to ~40%.
        best = np.zeros(lat.size)
        for blk in range(0, steps.size, 12):
            k = slice(blk, blk + 12)
            dx = sx[None, :] - tx[k, None]
            dy = sy[None, :] - ty[k, None]
            r = np.maximum(np.hypot(dx, dy), 1e-3)
            x = (rmax[k, None] / r) ** c.holland_b
            v = vmax[k, None] * np.sqrt(x * np.exp(1.0 - x))
            v += 0.5 * vt[k, None] * np.cos(np.arctan2(dx, dy) - (head[k, None] + np.pi / 2.0))
            v[r > c.max_radius_km] = 0.0
            np.maximum(best, v.max(axis=0), out=best)
        return best

    def footprint(self, event: Event, exposure: Exposure) -> Footprint:
        assert isinstance(event, TcEvent)
        c = self.cfg
        lat_t, lon_t = np.asarray(event.lat), np.asarray(event.lon)
        # probe every ~4 h (<= ~90 km at typical speeds) with the radius padded accordingly
        step = 4
        pad = float(np.max(event.vt)) * 3.6 * step / 2.0
        idx = exposure.within(lat_t[::step], lon_t[::step], c.max_radius_km + pad)
        idx = idx[exposure.perils[self.peril][idx]]
        if idx.size == 0:
            return empty_footprint(c.tau, c.phi)
        w = self.wind_at(event, exposure.lat[idx], exposure.lon[idx])
        keep = w >= max(c.min_median_wind, 1e-6)
        idx, w = idx[keep], w[keep]
        if idx.size == 0:
            return empty_footprint(c.tau, c.phi)
        lat0, lon0 = float(exposure.lat[idx].mean()), float(exposure.lon[idx].mean())
        x, y = local_xy_km(exposure.lat[idx], exposure.lon[idx], lat0, lon0)
        return Footprint(idx, np.log(w), c.tau, c.phi, x, y)

    # ------------------------------------------------------------------ scenarios / live
    def landfall_time(self, lat: F64, lon: F64, t: F64) -> tuple[float, float, float] | None:
        """First crossing of the coastal gate: (t, lat, lon) or None."""
        glat, glon, _ = self._gate_arrays()
        for k in range(len(t) - 1):
            p1, p2 = np.array([lon[k], lat[k]]), np.array([lon[k + 1], lat[k + 1]])
            for j in range(glat.size - 1):
                s = _seg_intersection(
                    p1, p2, np.array([glon[j], glat[j]]), np.array([glon[j + 1], glat[j + 1]])
                )
                if s is not None:
                    return (
                        float(t[k] + s * (t[k + 1] - t[k])),
                        float(lat[k] + s * (lat[k + 1] - lat[k])),
                        float(lon[k] + s * (lon[k + 1] - lon[k])),
                    )
        return None

    def track_event(
        self,
        points: list[dict[str, Any]],
        event_id: int,
        name: str,
        rate: float = 0.0,
        translation_ms: float | None = None,
    ) -> TcEvent:
        """Arbitrary track {lat, lon, vmax_ms, rmax_km?, t_h?} -> hourly event with decay."""
        if len(points) < 2:
            raise ValueError("a track needs at least two points")
        lat = np.array([float(p["lat"]) for p in points])
        lon = np.array([float(p["lon"]) for p in points])
        vmax = np.array([float(p["vmax_ms"]) for p in points])
        if all("t_h" in p for p in points):
            t = np.array([float(p["t_h"]) for p in points])
        else:
            vt = translation_ms or self.cfg.translation_ms
            seg = haversine_km(lat[:-1], lon[:-1], lat[1:], lon[1:])
            t = np.concatenate([[0.0], np.cumsum(seg / (vt * 3.6))])
        if np.any(np.diff(t) <= 0):
            raise ValueError("track times must be strictly increasing")
        th = np.arange(t[0], t[-1] + 1e-9, 1.0)
        hlat, hlon, hv = np.interp(th, t, lat), np.interp(th, t, lon), np.interp(th, t, vmax)
        rm = np.array(
            [
                float(p["rmax_km"])
                if p.get("rmax_km")
                else willoughby_rmax(float(p["vmax_ms"]), float(p["lat"]))
                for p in points
            ]
        )
        hr = np.interp(th, t, rm)
        if th.size < 2:
            th = t[[0, -1]]
            hlat, hlon, hv, hr = lat[[0, -1]], lon[[0, -1]], vmax[[0, -1]], rm[[0, -1]]
        heading = _bearing(hlat[:-1], hlon[:-1], hlat[1:], hlon[1:])
        heading = np.append(heading, heading[-1])
        dist = haversine_km(hlat[:-1], hlon[:-1], hlat[1:], hlon[1:])
        vt_arr = np.append(dist / (np.diff(th) * 3.6), dist[-1] / (np.diff(th)[-1] * 3.6))
        lf = self.landfall_time(hlat, hlon, th)
        v_land = float(np.interp(lf[0], th, hv)) if lf else float(hv.max())
        if lf:
            after = th > lf[0]
            hv[after] = np.minimum(hv[after], self.decay(v_land, th[after] - lf[0]))
        tr = {
            "t": th,
            "lat": hlat,
            "lon": hlon,
            "vmax": hv,
            "rmax": hr,
            "heading": heading,
            "vt": vt_arr,
        }
        return self._make(event_id, rate, name, tr, (lf[1], lf[2]) if lf else None, v_land)

    def event_from_spec(self, spec: dict[str, Any], event_id: int) -> Event:
        """Either {track: [...]} or landfall params {lat, lon, vmax_ms, heading_deg, rmax_km?}."""
        name = str(spec.get("name", "TC scenario"))
        if "track" in spec:
            return self.track_event(
                list(spec["track"]),
                event_id,
                name,
                float(spec.get("rate", 0.0)),
                spec.get("translation_ms"),
            )
        v0 = float(spec["vmax_ms"])
        lat, lon = float(spec["lat"]), float(spec["lon"])
        rmax = float(spec.get("rmax_km") or willoughby_rmax(v0, lat))
        heading = np.radians(float(spec.get("heading_deg", 315.0)))
        vt = float(spec.get("translation_ms", self.cfg.translation_ms))
        tr = self.straight_track(lat, lon, float(heading), vt, v0, rmax)
        return self._make(event_id, float(spec.get("rate", 0.0)), name, tr, (lat, lon), v0)
