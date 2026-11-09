"""Vectorised geodesy helpers (spherical Earth)."""

from __future__ import annotations

import h3
import numpy as np
import numpy.typing as npt

EARTH_RADIUS_KM = 6371.0088

FloatArray = npt.NDArray[np.float64]


def destination(
    lat_deg: FloatArray, lon_deg: FloatArray, bearing_rad: FloatArray, dist_km: FloatArray
) -> tuple[FloatArray, FloatArray]:
    """Great-circle destination point given start, initial bearing and distance."""
    phi1 = np.radians(lat_deg)
    lam1 = np.radians(lon_deg)
    delta = dist_km / EARTH_RADIUS_KM
    sin_phi2 = np.sin(phi1) * np.cos(delta) + np.cos(phi1) * np.sin(delta) * np.cos(bearing_rad)
    phi2 = np.arcsin(np.clip(sin_phi2, -1.0, 1.0))
    lam2 = lam1 + np.arctan2(
        np.sin(bearing_rad) * np.sin(delta) * np.cos(phi1),
        np.cos(delta) - np.sin(phi1) * sin_phi2,
    )
    lon2 = (np.degrees(lam2) + 540.0) % 360.0 - 180.0
    return np.degrees(phi2), lon2


def haversine_km(
    lat1: FloatArray, lon1: FloatArray, lat2: FloatArray, lon2: FloatArray
) -> FloatArray:
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    out: FloatArray = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))
    return out


def h3_cells(lat: FloatArray, lon: FloatArray, res: int) -> npt.NDArray[np.uint64]:
    """H3 cell index (as uint64) per point. ~1 us/point; vectorise in Rust later if hot."""
    f = h3.api.basic_int.latlng_to_cell
    return np.fromiter(
        (f(a, b, res) for a, b in zip(lat.tolist(), lon.tolist(), strict=True)),
        dtype=np.uint64,
        count=lat.size,
    )
