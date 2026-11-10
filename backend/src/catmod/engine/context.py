"""Columnar exposure view used by every engine kernel.

Loaded once per portfolio; all per-event work indexes into these flat arrays.
Spatial lookup uses a KD-tree over unit-sphere vectors, so radius queries are exact great-circle
neighbourhoods (chord length is monotone in arc length) with no projection artefacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.parquet as pq

from catmod import oed
from catmod.geo import EARTH_RADIUS_KM

F64 = npt.NDArray[np.float64]
I64 = npt.NDArray[np.int64]


def unit_vectors(lat: F64, lon: F64) -> F64:
    la, lo = np.radians(lat), np.radians(lon)
    return np.column_stack((np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)))


def chord(km: float) -> float:
    """Chord length on the unit sphere for a great-circle distance in km."""
    return float(2.0 * np.sin(min(km / EARTH_RADIUS_KM, np.pi) / 2.0))


def local_xy_km(lat: F64, lon: F64, lat0: float, lon0: float) -> tuple[F64, F64]:
    """Equirectangular projection about (lat0, lon0); adequate over a few hundred km."""
    k = np.pi / 180.0 * EARTH_RADIUS_KM
    return (lon - lon0) * k * np.cos(np.radians(lat0)), (lat - lat0) * k


@dataclass
class Exposure:
    """Flat arrays for one portfolio, plus segment and account indexing."""

    loc_number: npt.NDArray[np.object_]
    lat: F64
    lon: F64
    building: F64
    contents: F64
    bi: F64
    occupancy: I64
    construction: I64
    year_built: I64
    storeys: I64
    vs30: F64
    perils: dict[str, npt.NDArray[np.bool_]]
    ded: F64
    ded_type: I64
    limit: F64
    hub: npt.NDArray[np.object_]
    acc_idx: I64
    acc_number: npt.NDArray[np.object_]
    acc_participation: F64
    acc_limit: F64
    acc_attachment: F64
    acc_ded: F64
    _segments: tuple[npt.NDArray[np.object_], I64] | None = field(default=None, repr=False)

    @property
    def n(self) -> int:
        return int(self.lat.size)

    @property
    def tiv(self) -> F64:
        out: F64 = self.building + self.contents + self.bi
        return out

    # ---- spatial index: CSR buckets on a regular lat/lon grid + exact great-circle filter
    GRID_DEG = 0.5

    @cached_property
    def _grid(self) -> tuple[I64, I64, I64, F64]:
        """(sorted order, bucket starts, bucket keys, unit vectors)."""
        gi = np.floor((self.lat + 90.0) / self.GRID_DEG).astype(np.int64)
        gj = np.floor((self.lon + 180.0) / self.GRID_DEG).astype(np.int64)
        key = gi * 100_000 + gj
        order = np.argsort(key, kind="stable")
        keys, starts = np.unique(key[order], return_index=True)
        bounds = np.append(starts, order.size).astype(np.int64)
        return order, bounds, keys, unit_vectors(self.lat, self.lon)

    def within(self, lat: F64, lon: F64, radius_km: float) -> I64:
        """Sorted indices of locations within radius_km of ANY probe point (exact)."""
        order, starts, keys, xyz = self._grid
        if order.size == 0:
            return np.zeros(0, dtype=np.int64)
        dlat = radius_km / 111.0
        cells: set[int] = set()
        for la, lo in zip(np.atleast_1d(lat).tolist(), np.atleast_1d(lon).tolist(), strict=True):
            dlon = radius_km / (111.0 * max(np.cos(np.radians(min(abs(la) + dlat, 89.0))), 0.01))
            i0, i1 = (
                int(np.floor((la - dlat + 90) / self.GRID_DEG)),
                int(np.floor((la + dlat + 90) / self.GRID_DEG)),
            )
            j0, j1 = (
                int(np.floor((lo - dlon + 180) / self.GRID_DEG)),
                int(np.floor((lo + dlon + 180) / self.GRID_DEG)),
            )
            for i in range(i0, i1 + 1):
                base = i * 100_000
                cells.update(range(base + j0, base + j1 + 1))
        want = np.fromiter(cells, dtype=np.int64, count=len(cells))
        pos = np.flatnonzero(np.isin(keys, want))  # each present bucket exactly once
        if pos.size == 0:
            return np.zeros(0, dtype=np.int64)
        b = starts.tolist()
        cand = np.concatenate([order[b[p] : b[p + 1]] for p in pos.tolist()])
        probes = unit_vectors(np.atleast_1d(lat), np.atleast_1d(lon))
        cos_min = np.cos(radius_km / EARTH_RADIUS_KM)
        best = np.zeros(cand.size)
        for chunk in range(0, probes.shape[0], 64):
            best = np.maximum(best, (xyz[cand] @ probes[chunk : chunk + 64].T).max(axis=1))
        return np.sort(cand[best >= cos_min])

    @property
    def segments(self) -> tuple[npt.NDArray[np.object_], I64]:
        """Segment = (hub, occupancy, construction); returns (labels, per-location id)."""
        if self._segments is None:
            keys = np.char.add(
                np.char.add(self.hub.astype(str), "|"),
                np.char.add(
                    np.char.add(self.occupancy.astype(str), "|"), self.construction.astype(str)
                ),
            )
            labels, inv = np.unique(keys, return_inverse=True)
            self._segments = (labels.astype(object), inv.astype(np.int64))
        return self._segments

    @classmethod
    def from_tables(cls, loc: pa.Table, acc: pa.Table) -> Exposure:
        def col(name: str) -> npt.NDArray[np.float64]:
            return np.asarray(loc[name].to_numpy(zero_copy_only=False), dtype=np.float64)

        def icol(name: str) -> I64:
            return np.asarray(loc[name].to_numpy(zero_copy_only=False), dtype=np.int64)

        perils_str = np.asarray(loc["LocPerilsCovered"].to_pylist(), dtype=object)
        perils = {
            p: np.fromiter((p in s.split(";") for s in perils_str), bool, perils_str.size)
            for p in oed.PERILS
        }
        acc_numbers = np.asarray(acc["AccNumber"].to_pylist(), dtype=object)
        lookup = {a: i for i, a in enumerate(acc_numbers)}
        acc_idx = np.fromiter(
            (lookup[a] for a in loc["AccNumber"].to_pylist()), np.int64, loc.num_rows
        )
        vs30 = (
            col("catmod_vs30")
            if "catmod_vs30" in loc.schema.names
            else np.full(loc.num_rows, 400.0)
        )

        def acol(name: str) -> F64:
            return np.asarray(acc[name].to_numpy(zero_copy_only=False), dtype=np.float64)

        return cls(
            loc_number=np.asarray(loc["LocNumber"].to_pylist(), dtype=object),
            lat=col("Latitude"),
            lon=col("Longitude"),
            building=col("BuildingTIV") + col("OtherTIV"),
            contents=col("ContentsTIV"),
            bi=col("BITIV"),
            occupancy=icol("OccupancyCode"),
            construction=icol("ConstructionCode"),
            year_built=np.nan_to_num(col("YearBuilt"), nan=1980).astype(np.int64),
            storeys=np.nan_to_num(col("NumberOfStoreys"), nan=1).astype(np.int64),
            vs30=vs30,
            perils=perils,
            ded=np.nan_to_num(col("LocDed6All")),
            ded_type=np.nan_to_num(col("LocDedType6All")).astype(np.int64),
            limit=np.nan_to_num(col("LocLimit6All")),
            hub=np.asarray(
                [h or "" for h in loc["FlexiLocHub"].to_pylist()]
                if "FlexiLocHub" in loc.schema.names
                else [""] * loc.num_rows,
                dtype=object,
            ),
            acc_idx=acc_idx,
            acc_number=acc_numbers,
            acc_participation=acol("LayerParticipation"),
            acc_limit=acol("LayerLimit"),
            acc_attachment=acol("LayerAttachment"),
            acc_ded=np.nan_to_num(acol("PolDed6All")),
        )

    @classmethod
    def from_dir(cls, path: Path) -> Exposure:
        return cls.from_tables(
            pq.read_table(path / "location.parquet"), pq.read_table(path / "account.parquet")
        )
