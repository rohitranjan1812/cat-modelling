"""File-backed portfolio registry: one directory per portfolio with Parquet + manifest.json.

Survives restarts (manifests are rescanned on startup); queries run in DuckDB directly on
Parquet so nothing is held in memory between requests.
"""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel

from catmod.config import PortfolioConfig
from catmod.engine.context import Exposure
from catmod.exposure import generate
from catmod.exposure.io import (
    ACCOUNT_PARQUET,
    LOCATION_PARQUET,
    PortfolioSummary,
    summarize,
    write_portfolio,
)

Dimension = Literal["hub", "occupancy", "construction", "country", "peril", "h3"]

_DIM_SQL: Final[dict[str, str]] = {
    "hub": "FlexiLocHub",
    "occupancy": "CAST(OccupancyCode AS VARCHAR)",
    "construction": "CAST(ConstructionCode AS VARCHAR)",
    "country": "CountryCode",
}

MAP_COLUMNS: Final[list[str]] = [
    "LocNumber",
    "YearBuilt",
    "AccNumber",
    "Latitude",
    "Longitude",
    "OccupancyCode",
    "ConstructionCode",
    "LocPerilsCovered",
    "FlexiLocHub",
    "catmod_tiv",
]


class PortfolioMeta(BaseModel):
    id: str
    name: str
    created_at: datetime
    generation_ms: float
    config: PortfolioConfig
    summary: PortfolioSummary


class AggregateRow(BaseModel):
    key: str
    n_locations: int
    tiv: float
    lat: float
    lon: float


class NotFoundError(KeyError):
    pass


class PortfolioStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._items: dict[str, PortfolioMeta] = {}
        self._exposures: dict[str, Exposure] = {}
        for manifest in sorted(root.glob("*/manifest.json")):
            meta = PortfolioMeta.model_validate_json(manifest.read_text())
            self._items[meta.id] = meta

    def _dir(self, pid: str) -> Path:
        return self.root / pid

    def create(self, cfg: PortfolioConfig) -> PortfolioMeta:
        t0 = time.perf_counter()
        portfolio = generate(cfg)
        pid = uuid.uuid4().hex[:12]
        write_portfolio(portfolio, self._dir(pid), ("parquet",))
        meta = PortfolioMeta(
            id=pid,
            name=cfg.name,
            created_at=datetime.now(UTC),
            generation_ms=round((time.perf_counter() - t0) * 1e3, 1),
            config=cfg,
            summary=summarize(portfolio.locations, portfolio.accounts),
        )
        (self._dir(pid) / "manifest.json").write_text(meta.model_dump_json(indent=2))
        with self._lock:
            self._items[pid] = meta
        return meta

    def all(self) -> list[PortfolioMeta]:
        with self._lock:
            return sorted(self._items.values(), key=lambda m: m.created_at, reverse=True)

    def get(self, pid: str) -> PortfolioMeta:
        with self._lock:
            if pid not in self._items:
                raise NotFoundError(pid)
            return self._items[pid]

    def delete(self, pid: str) -> None:
        self.get(pid)
        with self._lock:
            del self._items[pid]
            self._exposures.pop(pid, None)
        shutil.rmtree(self._dir(pid), ignore_errors=True)

    def location_path(self, pid: str) -> Path:
        self.get(pid)
        return self._dir(pid) / LOCATION_PARQUET

    def account_path(self, pid: str) -> Path:
        self.get(pid)
        return self._dir(pid) / ACCOUNT_PARQUET

    def aggregate(self, pid: str, by: Dimension, h3_res: int | None = None) -> list[AggregateRow]:
        meta = self.get(pid)
        path = str(self.location_path(pid))
        if by == "h3":
            res = h3_res if h3_res is not None else meta.config.h3_resolutions[-1]
            if res not in meta.config.h3_resolutions:
                raise ValueError(
                    f"H3 resolution {res} not materialised; have {meta.config.h3_resolutions}"
                )
            source = "read_parquet(?)"
            key = f"lower(printf('%x', catmod_h3_r{res}))"
        elif by == "peril":
            source = (
                "(SELECT *, unnest(string_split(LocPerilsCovered, ';')) AS peril "
                "FROM read_parquet(?))"
            )
            key = "peril"
        else:
            source = "read_parquet(?)"
            key = _DIM_SQL[by]
        sql = (
            f"SELECT {key} AS key, count(*) AS n, sum(catmod_tiv) AS tiv, "
            f"avg(Latitude) AS lat, avg(Longitude) AS lon FROM {source} "
            "GROUP BY 1 ORDER BY tiv DESC"
        )
        rows = duckdb.connect().execute(sql, [path]).fetchall()
        return [
            AggregateRow(key=str(k), n_locations=int(n), tiv=float(t), lat=float(la), lon=float(lo))
            for k, n, t, la, lo in rows
        ]

    def exposure(self, pid: str) -> Exposure:
        """Engine view of a portfolio, cached (a handful of portfolios fit comfortably)."""
        self.get(pid)
        with self._lock:
            if pid in self._exposures:
                return self._exposures[pid]
        x = Exposure.from_dir(self._dir(pid))
        with self._lock:
            if len(self._exposures) >= 4:
                self._exposures.pop(next(iter(self._exposures)))
            self._exposures[pid] = x
        return x

    def map_table(self, pid: str) -> pa.Table:
        return pq.read_table(self.location_path(pid), columns=MAP_COLUMNS)
