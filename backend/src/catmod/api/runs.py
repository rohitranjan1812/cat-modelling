"""Analysis runs: persistence, background execution and the query surface behind the explorer."""

from __future__ import annotations

import shutil
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import duckdb
import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel

from catmod.api.hub import Hub
from catmod.api.store import NotFoundError, PortfolioStore
from catmod.engine.drill import EventDrill, drill
from catmod.engine.metrics import CurvePoint, PerspectiveMetrics, curve, perspective_metrics
from catmod.engine.runner import AnalysisConfig, Engine, RunSummary, run_analysis

Status = Literal["queued", "running", "done", "failed"]
Perspective = Literal["gu", "gross", "net"]
F64 = npt.NDArray[np.float64]


class RunMeta(BaseModel):
    id: str
    portfolio_id: str
    name: str
    status: Status
    progress: float = 0.0
    message: str = ""
    created_at: datetime
    finished_at: datetime | None = None
    config: AnalysisConfig
    summary: RunSummary | None = None
    error: str | None = None


class Filters(BaseModel):
    perils: list[str] = []
    hubs: list[str] = []
    occupancies: list[int] = []
    constructions: list[int] = []

    @property
    def segmented(self) -> bool:
        return bool(self.hubs or self.occupancies or self.constructions)


class EPResponse(BaseModel):
    perspective: Perspective
    filters: Filters
    n_years: int
    metrics: PerspectiveMetrics
    curve: list[CurvePoint]


class BreakdownRow(BaseModel):
    key: str
    n_locations: int
    tiv: float
    aal_gu: float
    aal_gross: float
    lat: float
    lon: float


class EventRow(BaseModel):
    event_id: int
    peril: str
    name: str
    rate: float
    lat: float
    lon: float
    magnitude: float | None = None
    vmax_ms: float | None = None
    n_affected: int
    max_intensity: float
    mean_gu: float
    mean_gross: float
    sd_gross: float
    max_gross: float
    rp_equiv: float | None = None  # 1 / rate of events at least as large (by mean gross)


class EventsAtRP(BaseModel):
    rp: float
    curve: Literal["oep", "aep"]
    perspective: Perspective
    level: float
    events: list[EventRow]
    occurrences: list[int]  # matching occurrence counts per event (same order)


DIMENSIONS = {
    "hub": "FlexiLocHub",
    "occupancy": "OccupancyCode",
    "construction": "ConstructionCode",
    "country": "CountryCode",
}


class RunStore:
    def __init__(self, root: Path, portfolios: PortfolioStore, hub: Hub) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.portfolios = portfolios
        self.hub = hub
        self._lock = threading.Lock()
        self._items: dict[str, RunMeta] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="catmod-run")
        self._engines: dict[str, Engine] = {}
        for manifest in sorted(root.glob("*/manifest.json")):
            meta = RunMeta.model_validate_json(manifest.read_text())
            if meta.status in ("queued", "running"):
                meta = meta.model_copy(update={"status": "failed", "error": "interrupted"})
            self._items[meta.id] = meta

    # ------------------------------------------------------------------ lifecycle
    def _save(self, meta: RunMeta) -> None:
        d = self.root / meta.id
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(meta.model_dump_json(indent=2))
        with self._lock:
            self._items[meta.id] = meta

    def _update(self, rid: str, **kw: Any) -> RunMeta:
        meta = self.get(rid).model_copy(update=kw)
        self._save(meta)
        return meta

    def submit(self, portfolio_id: str, cfg: AnalysisConfig, wait: bool = False) -> RunMeta:
        self.portfolios.get(portfolio_id)
        rid = uuid.uuid4().hex[:12]
        meta = RunMeta(
            id=rid,
            portfolio_id=portfolio_id,
            name=cfg.name,
            status="queued",
            created_at=datetime.now(UTC),
            config=cfg,
        )
        self._save(meta)
        self.hub.publish("run.status", meta.model_dump(exclude={"config"}))
        fut = self._pool.submit(self._execute, rid)
        if wait:
            fut.result()
            return self.get(rid)
        return meta

    def _execute(self, rid: str) -> None:
        meta = self._update(rid, status="running", message="starting")
        last = [0.0]

        def progress(frac: float, msg: str) -> None:
            if frac - last[0] >= 0.02 or frac >= 1.0:
                last[0] = frac
                m = self._update(rid, progress=round(frac, 3), message=msg)
                self.hub.publish(
                    "run.progress", {"id": rid, "progress": m.progress, "message": msg}
                )

        try:
            exposure = self.portfolios.exposure(meta.portfolio_id)
            result = run_analysis(exposure, meta.config, progress)
            result.save(self.root / rid)
            done = self._update(
                rid,
                status="done",
                progress=1.0,
                message="done",
                summary=result.summary,
                finished_at=datetime.now(UTC),
            )
            self.hub.publish("run.status", done.model_dump(exclude={"config"}))
        except Exception as e:  # pragma: no cover - surfaced to the client
            failed = self._update(
                rid,
                status="failed",
                error=f"{e}\n{traceback.format_exc()}",
                finished_at=datetime.now(UTC),
            )
            self.hub.publish("run.status", failed.model_dump(exclude={"config"}))

    def all(self, portfolio_id: str | None = None) -> list[RunMeta]:
        with self._lock:
            items = list(self._items.values())
        if portfolio_id:
            items = [m for m in items if m.portfolio_id == portfolio_id]
        return sorted(items, key=lambda m: m.created_at, reverse=True)

    def get(self, rid: str) -> RunMeta:
        with self._lock:
            if rid not in self._items:
                raise NotFoundError(rid)
            return self._items[rid]

    def done(self, rid: str) -> RunMeta:
        meta = self.get(rid)
        if meta.status != "done":
            raise ValueError(f"run {rid} is {meta.status}")
        return meta

    def delete(self, rid: str) -> None:
        self.get(rid)
        with self._lock:
            del self._items[rid]
        self._engines.pop(rid, None)
        _table.cache_clear()
        shutil.rmtree(self.root / rid, ignore_errors=True)

    def path(self, rid: str, name: str) -> Path:
        self.done(rid)
        return self.root / rid / f"{name}.parquet"

    def engine(self, rid: str) -> Engine:
        meta = self.done(rid)
        if rid not in self._engines:
            self._engines[rid] = Engine(self.portfolios.exposure(meta.portfolio_id), meta.config)
        return self._engines[rid]

    # ------------------------------------------------------------------ queries
    def _occurrence_losses(
        self, rid: str, perspective: Perspective, f: Filters
    ) -> tuple[npt.NDArray[np.int64], F64]:
        ylt = _table(self.path(rid, "ylt"))
        year = ylt["year"].to_numpy()
        eid = ylt["event_id"].to_numpy()
        if f.segmented:
            if perspective == "net":
                raise ValueError(
                    "net losses are not separable by segment (reinsurance is "
                    "non-linear); use gu or gross with segment filters"
                )
            con = duckdb.connect()
            con.register("seg", _table(self.path(rid, "segments")))
            con.register("lab", _table(self.path(rid, "segment_labels")))
            where: list[str] = []
            params: list[Any] = []
            for col, vals in (
                ("hub", f.hubs),
                ("occupancy", f.occupancies),
                ("construction", f.constructions),
            ):
                if vals:
                    where.append(f"lab.{col} IN ({','.join('?' * len(vals))})")
                    params.extend(vals)
            rows = con.execute(
                f"SELECT seg.event_id * 4096 + seg.sample AS k, sum(seg.{perspective}) "
                f"FROM seg JOIN lab USING (segment) WHERE {' AND '.join(where)} "
                "GROUP BY 1 ORDER BY 1",
                params,
            ).fetchnumpy()
            keys = np.asarray(rows["k"], dtype=np.int64)
            seg_vals = np.asarray(rows[next(k for k in rows if k != "k")], dtype=np.float64)
            occ_key = eid * 4096 + ylt["sample"].to_numpy()
            loss = np.zeros(eid.size)
            if keys.size:
                pos = np.clip(np.searchsorted(keys, occ_key), 0, keys.size - 1)
                hit = keys[pos] == occ_key
                loss[hit] = seg_vals[pos[hit]]
        else:
            loss = ylt[perspective].to_numpy()
        if f.perils:
            ev = _table(self.path(rid, "events"))
            keep_ids = np.asarray(ev["event_id"].to_numpy())[
                np.isin(np.asarray(ev["peril"].to_pylist()), f.perils)
            ]
            loss = np.where(np.isin(eid, keep_ids), loss, 0.0)
        return year, np.asarray(loss, dtype=np.float64)

    def ep(self, rid: str, perspective: Perspective, f: Filters) -> EPResponse:
        meta = self.done(rid)
        year, loss = self._occurrence_losses(rid, perspective, f)
        n = meta.config.n_years
        return EPResponse(
            perspective=perspective,
            filters=f,
            n_years=n,
            metrics=perspective_metrics(year, loss, n, meta.config.return_periods),
            curve=curve(year, loss, n),
        )

    def breakdown(self, rid: str, by: str, f: Filters, h3_res: int | None) -> list[BreakdownRow]:
        meta = self.done(rid)
        pmeta = self.portfolios.get(meta.portfolio_id)
        loc = _table(self.portfolios.location_path(meta.portfolio_id))
        aal = _table(self.path(rid, "loc_aal"))
        perils = f.perils or meta.config.perils
        gu = sum(
            (aal[f"aal_gu_{p}"].to_numpy() for p in perils if f"aal_gu_{p}" in aal.schema.names),
            np.zeros(loc.num_rows),
        )
        gross = sum(
            (
                aal[f"aal_gross_{p}"].to_numpy()
                for p in perils
                if f"aal_gross_{p}" in aal.schema.names
            ),
            np.zeros(loc.num_rows),
        )
        mask = np.ones(loc.num_rows, bool)
        for col, vals in (
            (DIMENSIONS["hub"], f.hubs),
            (DIMENSIONS["occupancy"], f.occupancies),
            (DIMENSIONS["construction"], f.constructions),
        ):
            if vals:
                mask &= np.isin(np.asarray(loc[col].to_pylist()), vals)
        lat, lon = loc["Latitude"].to_numpy(), loc["Longitude"].to_numpy()
        tiv = loc["catmod_tiv"].to_numpy()
        if by == "peril":
            out = []
            for p in perils:
                covered = np.asarray(
                    [p in s.split(";") for s in loc["LocPerilsCovered"].to_pylist()]
                )
                m = mask & covered
                out.append(
                    BreakdownRow(
                        key=p,
                        n_locations=int(m.sum()),
                        tiv=float(tiv[m].sum()),
                        aal_gu=float(aal[f"aal_gu_{p}"].to_numpy()[mask].sum()),
                        aal_gross=float(aal[f"aal_gross_{p}"].to_numpy()[mask].sum()),
                        lat=float(lat[m].mean()) if m.any() else 0.0,
                        lon=float(lon[m].mean()) if m.any() else 0.0,
                    )
                )
            return sorted(out, key=lambda r: -r.aal_gu)
        if by == "h3":
            res = h3_res if h3_res is not None else pmeta.config.h3_resolutions[-1]
            if res not in pmeta.config.h3_resolutions:
                raise ValueError(f"H3 resolution {res} not materialised")
            keys = np.asarray([format(int(v), "x") for v in loc[f"catmod_h3_r{res}"].to_numpy()])
        elif by in DIMENSIONS:
            keys = np.asarray([str(v) for v in loc[DIMENSIONS[by]].to_pylist()])
        else:
            raise ValueError(f"unknown dimension {by!r}")
        keys = keys[mask]
        uk, inv = np.unique(keys, return_inverse=True)
        cnt = np.bincount(inv)
        rows = [
            BreakdownRow(
                key=str(k),
                n_locations=int(c),
                tiv=float(t),
                aal_gu=float(g),
                aal_gross=float(r),
                lat=float(la),
                lon=float(lo),
            )
            for k, c, t, g, r, la, lo in zip(
                uk,
                cnt,
                np.bincount(inv, tiv[mask]),
                np.bincount(inv, gu[mask]),
                np.bincount(inv, gross[mask]),
                np.bincount(inv, lat[mask]) / cnt,
                np.bincount(inv, lon[mask]) / cnt,
                strict=True,
            )
        ]
        return sorted(rows, key=lambda r: -r.aal_gu)

    def events(
        self, rid: str, peril: str | None, sort: str, limit: int, offset: int, min_loss: float = 0.0
    ) -> tuple[int, list[EventRow]]:
        self.done(rid)
        ev = _table(self.path(rid, "events"))
        if ev.num_rows == 0:
            return 0, []
        rows = ev.to_pylist()
        rows.sort(key=lambda r: -r["mean_gross"])
        cum = np.cumsum([r["rate"] for r in rows])
        for r, c in zip(rows, cum, strict=True):
            r["rp_equiv"] = float(1.0 / c) if c > 0 else None
        if peril:
            rows = [r for r in rows if r["peril"] == peril]
        if min_loss:
            rows = [r for r in rows if r["mean_gross"] >= min_loss]
        if sort not in EventRow.model_fields:
            raise ValueError(f"cannot sort by {sort!r}")
        if sort in ("name", "peril"):
            rows.sort(key=lambda r: str(r.get(sort)))
        else:
            rows.sort(key=lambda r: -float(r.get(sort) or 0.0))
        return len(rows), [EventRow.model_validate(r) for r in rows[offset : offset + limit]]

    def events_at_rp(
        self,
        rid: str,
        rp: float,
        which: Literal["oep", "aep"],
        perspective: Perspective,
        window: float,
        limit: int,
    ) -> EventsAtRP:
        meta = self.done(rid)
        year, loss = self._occurrence_losses(rid, perspective, Filters())
        n = meta.config.n_years
        agg = np.bincount(year, loss, n)
        occ = np.zeros(n)
        np.maximum.at(occ, year, loss)
        series = occ if which == "oep" else agg
        k = int(np.ceil(n / rp - 1e-9)) - 1
        level = float(np.sort(series)[::-1][k]) if 0 <= k < n else 0.0
        ylt = _table(self.path(rid, "ylt"))
        eid = ylt["event_id"].to_numpy()
        if which == "oep":
            sel = (loss >= level * (1 - window)) & (loss <= level * (1 + window))
            ids, counts = np.unique(eid[sel], return_counts=True)
        else:
            years = np.flatnonzero((agg >= level * (1 - window)) & (agg <= level * (1 + window)))
            sel = np.isin(year, years)
            ids, counts = np.unique(eid[sel], return_counts=True)
        order = np.argsort(-counts)[:limit]
        _, all_rows = self.events(rid, None, "mean_gross", 10**9, 0)
        by_id = {r.event_id: r for r in all_rows}
        evs = [by_id[int(i)] for i in ids[order] if int(i) in by_id]
        return EventsAtRP(
            rp=rp,
            curve=which,
            perspective=perspective,
            level=level,
            events=evs,
            occurrences=[int(c) for c in counts[order]],
        )

    def event_detail(self, rid: str, event_id: int, top: int = 100) -> EventDrill:
        eng = self.engine(rid)
        ev = eng.event_by_id(event_id)
        el = eng.event_loss(ev)
        return drill(eng.x, el, eng.hazards[ev.peril].unit, top=top)

    def locations_arrow(self, rid: str) -> pa.Table:
        meta = self.done(rid)
        loc = _table(self.portfolios.location_path(meta.portfolio_id))
        aal = _table(self.path(rid, "loc_aal"))
        cols = [
            "LocNumber",
            "AccNumber",
            "Latitude",
            "Longitude",
            "OccupancyCode",
            "ConstructionCode",
            "YearBuilt",
            "LocPerilsCovered",
            "FlexiLocHub",
            "catmod_tiv",
        ]
        cols += [c for c in loc.schema.names if c.startswith("catmod_h3_r")]
        t = loc.select(cols)
        for name in aal.schema.names:
            if name.startswith("aal_"):
                t = t.append_column(name, aal[name])
        return t


@lru_cache(maxsize=64)
def _table(path: Path) -> pa.Table:
    if not path.exists():
        raise NotFoundError(str(path))
    return pq.read_table(path)
