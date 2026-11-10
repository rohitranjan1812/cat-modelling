"""Live hazard feeds -> engine event specs.

USGS (earthquakes, GeoJSON summary feeds): point source (lat, lon, depth, Mw). The fault plane is
unknown at first report, so the nowcast is an ensemble over ``STRIKES`` rupture orientations.

NOAA NHC (CurrentStorms.json): current position, 1-min max wind (kt), motion direction (deg,
toward) and speed. We build a persistence track: straight-line motion 12 h back and 72 h forward
at constant intensity; the engine detects landfall on the coastal gate and applies inland decay.
NHC reports ``movementSpeed`` in mph (public advisory convention); ``NHC_SPEED_UNIT`` makes the
assumption explicit and adjustable.
"""

from __future__ import annotations

import json
import os
import urllib.request
import zlib
from datetime import UTC, datetime
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel

from catmod.engine.eq import rupture_length_km
from catmod.engine.tc import KT_TO_MS, MPH_TO_MS
from catmod.geo import destination

USGS_URL = os.environ.get(
    "CATMOD_USGS_URL",
    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson",
)
NHC_URL = os.environ.get("CATMOD_NHC_URL", "https://www.nhc.noaa.gov/CurrentStorms.json")
NHC_SPEED_UNIT = os.environ.get("CATMOD_NHC_SPEED_UNIT", "mph")
STRIKES = (0.0, 45.0, 90.0, 135.0)
USER_AGENT = "catmod-live/0.2 (+https://github.com/rohitranjan1812/cat-modelling)"

Source = Literal["usgs", "nhc", "simulated"]


class LiveEvent(BaseModel):
    id: str
    source: Source
    peril: str
    name: str
    time: datetime
    lat: float
    lon: float
    magnitude: float | None = None
    depth_km: float | None = None
    vmax_ms: float | None = None
    classification: str | None = None
    url: str | None = None
    geometry: dict[str, Any]
    specs: list[dict[str, Any]]  # ensemble members (engine event specs)


def stable_event_id(key: str) -> int:
    return 8_000_000 + zlib.crc32(key.encode()) % 900_000


def fetch_json(url: str, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def eq_specs(lat: float, lon: float, mag: float, depth: float, name: str) -> list[dict[str, Any]]:
    return [
        {
            "lat": lat,
            "lon": lon,
            "magnitude": mag,
            "depth_km": depth,
            "strike_deg": s,
            "name": name,
        }
        for s in STRIKES
    ]


def eq_geometry(lat: float, lon: float, mag: float) -> dict[str, Any]:
    half = np.array([rupture_length_km(mag) / 2.0])
    lines = []
    for s in STRIKES:
        a = destination(np.array([lat]), np.array([lon]), np.radians(np.array([s])), half)
        b = destination(np.array([lat]), np.array([lon]), np.radians(np.array([s + 180])), half)
        lines.append([[float(a[1][0]), float(a[0][0])], [float(b[1][0]), float(b[0][0])]])
    return {"type": "MultiLineString", "coordinates": lines}


def parse_usgs(doc: dict[str, Any], min_mag: float = 4.5) -> list[LiveEvent]:
    out = []
    for f in doc.get("features", []):
        p, g = f.get("properties", {}), f.get("geometry", {})
        mag = p.get("mag")
        if mag is None or mag < min_mag or p.get("type", "earthquake") != "earthquake":
            continue
        lon, lat, depth = [*list(g.get("coordinates", [])), 10.0][:3]
        depth = float(depth if depth is not None else 10.0)
        name = p.get("title") or f"M{mag} {p.get('place', '')}"
        out.append(
            LiveEvent(
                id=f"usgs:{f.get('id')}",
                source="usgs",
                peril="QEQ",
                name=name,
                time=datetime.fromtimestamp((p.get("time") or 0) / 1000, UTC),
                lat=float(lat),
                lon=float(lon),
                magnitude=float(mag),
                depth_km=depth,
                url=p.get("url"),
                geometry=eq_geometry(float(lat), float(lon), float(mag)),
                specs=eq_specs(float(lat), float(lon), float(mag), depth, name),
            )
        )
    return out


def tc_track(
    lat: float,
    lon: float,
    heading_deg: float,
    speed_ms: float,
    vmax_ms: float,
    back_h: float = 12.0,
    fwd_h: float = 72.0,
) -> list[dict[str, Any]]:
    hd = np.radians(heading_deg)
    pts = []
    for t in (-back_h, 0.0, fwd_h / 2, fwd_h):
        d = abs(t) * speed_ms * 3.6
        b = hd if t >= 0 else hd + np.pi
        la, lo = destination(np.array([lat]), np.array([lon]), np.array([b]), np.array([d]))
        pts.append({"lat": float(la[0]), "lon": float(lo[0]), "vmax_ms": vmax_ms, "t_h": t})
    return pts


def parse_nhc(doc: dict[str, Any]) -> list[LiveEvent]:
    out = []
    unit = MPH_TO_MS if NHC_SPEED_UNIT == "mph" else KT_TO_MS
    for s in doc.get("activeStorms", []):
        try:
            lat, lon = float(s["latitudeNumeric"]), float(s["longitudeNumeric"])
            vmax = float(s["intensity"]) * KT_TO_MS
            heading = float(s.get("movementDir") or 0.0)
            speed = max(float(s.get("movementSpeed") or 0.0) * unit, 1.0)
        except (KeyError, TypeError, ValueError):
            continue
        name = f"{s.get('classification', 'TC')} {s.get('name', s.get('id'))}"
        track = tc_track(lat, lon, heading, speed, vmax)
        out.append(
            LiveEvent(
                id=f"nhc:{s.get('id')}",
                source="nhc",
                peril="WTC",
                name=name,
                time=datetime.fromisoformat(
                    str(s.get("lastUpdate", datetime.now(UTC).isoformat())).replace("Z", "+00:00")
                ),
                lat=lat,
                lon=lon,
                vmax_ms=round(vmax, 1),
                classification=s.get("classification"),
                url=(s.get("publicAdvisory") or {}).get("url"),
                geometry={
                    "type": "LineString",
                    "coordinates": [[p["lon"], p["lat"]] for p in track],
                },
                specs=[{"track": track, "name": name}],
            )
        )
    return out
