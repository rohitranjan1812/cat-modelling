"""Analysis configuration, the event-loss kernel, and stochastic run orchestration.

Kernel (one event, S samples; every random draw seeded by (run seed, event_id), so any event
can be recomputed bit-for-bit later for drill-down):

    ln I   = ln I_med + tau * eta  + phi * eps(x)        eta ~ N(0,1) per sample,
                                                         eps ~ spatial field (pluggable)
    MDR    = vulnerability curve(I)
    z      = damage copula score (pluggable)             independent | one_factor | spatial
    DR     = F^{-1}_Beta(MDR)(Phi(z))
    GU     = DR_b * building + DR_c * contents + DR_bi * BI
    gross  = account layer(location terms(GU))           back-allocated to locations

Run:  ELT (event x sample totals) -> Poisson YLT -> reinsurance -> metrics, plus exact location
AAL (sum_e rate_e * mean_s loss) and per-segment event-sample losses for filtered EP curves.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, Field, field_validator

from catmod import oed
from catmod.config import StrictModel
from catmod.engine.context import Exposure
from catmod.engine.copulas import DamageCopula, make_copula
from catmod.engine.eq import EqConfig
from catmod.engine.fields import SpatialField, make_field
from catmod.engine.financial import (
    ReinsuranceConfig,
    account_gross,
    apply_reinsurance,
    location_net,
)
from catmod.engine.hazard import Event, Footprint, Hazard
from catmod.engine.metrics import DEFAULT_RPS, PerspectiveMetrics, perspective_metrics
from catmod.engine.tc import TcConfig
from catmod.engine.vulnerability import Vulnerability, VulnerabilityConfig

F64 = npt.NDArray[np.float64]
I64 = npt.NDArray[np.int64]
Progress = Callable[[float, str], None]


class DamageCorrelationConfig(StrictModel):
    model: str = Field(default="one_factor", description="independent | one_factor | spatial")
    rho: float = Field(default=0.15, ge=0, le=1)
    rho_by_construction: dict[int, float] = Field(default_factory=dict)
    range_km: float = Field(default=10.0, gt=0)
    field: str = "fft"

    def params(self) -> dict[str, Any]:
        return self.model_dump(exclude={"model"})


class AnalysisConfig(StrictModel):
    name: str = Field(default="Stochastic analysis", min_length=1, max_length=120)
    perils: list[str] = Field(default_factory=lambda: ["QEQ", "WTC"], min_length=1)
    n_samples: int = Field(default=8, ge=1, le=512, description="Samples per event")
    n_years: int = Field(default=50_000, ge=100, le=2_000_000)
    seed: int = Field(default=7, ge=0)
    eq: EqConfig = EqConfig()
    tc: TcConfig = TcConfig()
    plugins: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="Config for plugin hazards keyed by peril code"
    )
    damage_correlation: DamageCorrelationConfig = DamageCorrelationConfig()
    vulnerability: VulnerabilityConfig = VulnerabilityConfig()
    reinsurance: ReinsuranceConfig = ReinsuranceConfig()
    return_periods: list[int] = Field(default_factory=lambda: list(DEFAULT_RPS))
    workers: int = Field(default=0, ge=0, le=64, description="Processes; 0 = auto (<= 8)")

    @field_validator("perils")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        from catmod.engine.registry import hazards

        unknown = sorted(set(v) - set(hazards()))
        if unknown:
            raise ValueError(f"no hazard registered for {unknown}; have {sorted(hazards())}")
        return v


def compact(ids: I64, universe: int) -> tuple[I64, I64]:
    """O(universe) equivalent of np.unique(ids, return_inverse=True) for bounded int ids."""
    mark = np.zeros(universe, dtype=np.int64)
    mark[ids] = 1
    present = np.flatnonzero(mark)
    rank = np.cumsum(mark) - 1
    return present, rank[ids]


# ---------------------------------------------------------------------------- catalogue cache
_CATALOGS: dict[str, list[Event]] = {}


def _hash(obj: BaseModel | dict[str, Any]) -> str:
    raw = obj.model_dump_json() if isinstance(obj, BaseModel) else json.dumps(obj, sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


@dataclass
class EventLoss:
    event: Event
    footprint: Footprint
    gu: F64  # (S,)
    gross: F64  # (S,)
    loc_gu: F64  # (S, n_aff)
    loc_gross: F64  # (S, n_aff)

    @property
    def idx(self) -> I64:
        return self.footprint.idx


class Engine:
    def __init__(self, exposure: Exposure, cfg: AnalysisConfig) -> None:
        from catmod.engine.registry import hazards

        self.x = exposure
        self.cfg = cfg
        reg = hazards()
        self.hazards: dict[str, Hazard] = {}
        for p in cfg.perils:
            conf: Any = cfg.eq if p == "QEQ" else cfg.tc if p == "WTC" else cfg.plugins.get(p)
            self.hazards[p] = reg[p](conf)
        self.fields: dict[str, SpatialField] = {
            p: make_field(**h.field_params()) for p, h in self.hazards.items()
        }
        dc = cfg.damage_correlation
        self.copula: DamageCopula = make_copula(dc.model, **dc.params())
        self.vuln = Vulnerability(cfg.vulnerability)

    @cached_property
    def _curve_params(self) -> dict[str, tuple[F64, F64]]:
        return {
            p: self.vuln.params(p, self.x.construction, self.x.year_built) for p in self.hazards
        }

    @cached_property
    def _tiv(self) -> F64:
        return self.x.tiv

    def catalog(self) -> list[Event]:
        out: list[Event] = []
        for p, h in self.hazards.items():
            conf = self.cfg.eq if p == "QEQ" else self.cfg.tc if p == "WTC" else self.cfg.plugins
            key = f"{p}:{_hash(conf)}"
            if key not in _CATALOGS:
                _CATALOGS[key] = h.catalog()
            out.extend(_CATALOGS[key])
        return out

    def event_by_id(self, event_id: int) -> Event:
        for ev in self.catalog():
            if ev.event_id == event_id:
                return ev
        raise KeyError(event_id)

    def event_loss(self, event: Event, n_samples: int | None = None) -> EventLoss:
        s = n_samples or self.cfg.n_samples
        hz = self.hazards[event.peril]
        fp = hz.footprint(event, self.x)
        n = fp.idx.size
        if n == 0:
            z = np.zeros(s)
            e = np.zeros((s, 0))
            return EventLoss(event, fp, z, z.copy(), e, e.copy())
        rng = np.random.default_rng([self.cfg.seed, event.event_id])
        idx = fp.idx
        eta = rng.standard_normal((s, 1)) * fp.tau
        eps = self.fields[event.peril].sample(rng, fp.x_km, fp.y_km, s) * fp.phi
        intensity = np.exp(fp.ln_median + eta + eps)
        theta, beta = self._curve_params[event.peril]
        mdr = self.vuln.mdr(intensity, theta[idx], beta[idx])
        zc = self.copula.sample(rng, fp.x_km, fp.y_km, self.x.construction[idx], s)
        dr = self.vuln.sample(mdr, zc)
        rb, rc, rbi = self.vuln.coverage_ratios(dr)
        gu = rb * self.x.building[idx] + rc * self.x.contents[idx] + rbi * self.x.bi[idx]
        net = location_net(
            gu, self._tiv[idx], self.x.ded[idx], self.x.ded_type[idx], self.x.limit[idx]
        )
        acc_u, acc_local = compact(self.x.acc_idx[idx], self.x.acc_number.size)
        loc_gross, acc_gross = account_gross(
            net,
            acc_local,
            acc_u.size,
            self.x.acc_ded[acc_u],
            self.x.acc_attachment[acc_u],
            self.x.acc_limit[acc_u],
            self.x.acc_participation[acc_u],
        )
        return EventLoss(event, fp, gu.sum(axis=1), acc_gross.sum(axis=1), gu, loc_gross)


# ---------------------------------------------------------------------------- results
class RunSummary(BaseModel):
    n_events: int
    n_loss_events: int
    n_occurrences: int
    n_years: int
    elapsed_s: float
    aal_elt: dict[str, float]  # analytic AAL from ELT means (sim-free check)
    metrics: dict[str, PerspectiveMetrics]  # gu, gross, net


@dataclass
class RunResult:
    summary: RunSummary
    events: pa.Table
    samples: pa.Table  # event_id, sample, gu, gross
    segments: pa.Table  # event_id, sample, segment, gu, gross
    segment_labels: pa.Table  # segment, hub, occupancy, construction
    ylt: pa.Table  # year, event_id, sample, gu, gross, ceded, net
    loc_aal: pa.Table  # loc_index, LocNumber, aal_gu, aal_gross

    def save(self, out: Path) -> None:
        out.mkdir(parents=True, exist_ok=True)
        for name in ("events", "samples", "segments", "segment_labels", "ylt", "loc_aal"):
            pq.write_table(getattr(self, name), out / f"{name}.parquet", compression="zstd")
        (out / "summary.json").write_text(self.summary.model_dump_json(indent=2))


def simulate_years(
    rates: F64, n_samples: int, n_years: int, rng: np.random.Generator
) -> tuple[I64, I64, I64]:
    """Poisson occurrence simulation. Returns (year, event_row, sample) sorted by year."""
    lam = float(rates.sum())
    if lam <= 0:
        z = np.zeros(0, dtype=np.int64)
        return z, z, z
    counts = rng.poisson(lam, size=n_years)
    year = np.repeat(np.arange(n_years, dtype=np.int64), counts)
    ev = rng.choice(rates.size, size=year.size, p=rates / lam)
    sample = rng.integers(0, n_samples, size=year.size)
    return year, ev.astype(np.int64), sample.astype(np.int64)


_N_CHUNKS = 64
_WORKER: dict[str, Any] = {}


@dataclass
class _ChunkResult:
    n_events: int
    rows: list[dict[str, Any]]
    gu: list[F64]
    gross: list[F64]
    segs: list[tuple[int, I64, F64, F64]]
    loc_aal: dict[str, F64]  # f"{perspective}_{peril}" -> per-location AAL


def _init_worker(exposure: Exposure, cfg: AnalysisConfig) -> None:
    eng = Engine(exposure, cfg)
    _WORKER["engine"], _WORKER["catalog"] = eng, eng.catalog()


def _run_chunk(event_rows: list[int]) -> _ChunkResult:
    eng: Engine = _WORKER["engine"]
    catalog: list[Event] = _WORKER["catalog"]
    s = eng.cfg.n_samples
    seg_labels, seg_of_loc = eng.x.segments
    out = _ChunkResult(
        len(event_rows),
        [],
        [],
        [],
        [],
        {f"{k}_{p}": np.zeros(eng.x.n) for k in ("gu", "gross") for p in eng.hazards},
    )
    for k in event_rows:
        ev = catalog[k]
        el = eng.event_loss(ev, s)
        if el.idx.size == 0 or float(np.max(el.gu)) <= 0:
            continue
        out.loc_aal[f"gu_{ev.peril}"][el.idx] += ev.rate * el.loc_gu.mean(axis=0)
        out.loc_aal[f"gross_{ev.peril}"][el.idx] += ev.rate * el.loc_gross.mean(axis=0)
        segs, inv = compact(seg_of_loc[el.idx], seg_labels.size)
        flat = (inv[None, :] + segs.size * np.arange(s)[:, None]).ravel()
        sg = np.bincount(flat, el.loc_gu.ravel(), s * segs.size).reshape(s, segs.size)
        sr = np.bincount(flat, el.loc_gross.ravel(), s * segs.size).reshape(s, segs.size)
        out.segs.append((ev.event_id, segs, sg, sr))
        out.rows.append(
            {
                "event_id": ev.event_id,
                "peril": ev.peril,
                "rate": ev.rate,
                "name": ev.name,
                **ev.summary(),
                "n_affected": int(el.idx.size),
                "max_intensity": float(np.exp(np.max(el.footprint.ln_median))),
                "mean_gu": float(el.gu.mean()),
                "sd_gu": float(el.gu.std()),
                "mean_gross": float(el.gross.mean()),
                "sd_gross": float(el.gross.std()),
                "max_gross": float(np.max(el.gross)),
            }
        )
        out.gu.append(el.gu)
        out.gross.append(el.gross)
    return out


def _map_chunks(
    eng: Engine, catalog: list[Event], chunks: list[list[int]], workers: int
) -> Iterator[_ChunkResult]:
    """Event chunks in-process, or on a spawn-based process pool.

    ``spawn`` (not ``fork``): the API runs analyses from a multi-threaded server process, and
    forking a threaded process can deadlock children. Each worker rebuilds the engine from the
    pickled exposure + config (catalogues are deterministic). Results are independent of the
    worker count because every event uses its own seeded generator.
    """
    if workers <= 1 or len(chunks) <= 1:
        _WORKER["engine"], _WORKER["catalog"] = eng, catalog
        try:
            yield from map(_run_chunk, chunks)
        finally:
            _WORKER.clear()
        return
    ctx = mp.get_context("spawn")
    with ctx.Pool(workers, initializer=_init_worker, initargs=(eng.x, eng.cfg)) as pool:
        yield from pool.imap_unordered(_run_chunk, chunks)


def run_analysis(
    exposure: Exposure, cfg: AnalysisConfig, progress: Progress | None = None
) -> RunResult:
    t0 = time.perf_counter()
    eng = Engine(exposure, cfg)
    report = progress or (lambda f, m: None)
    report(0.0, "building catalogue")
    catalog = eng.catalog()
    s = cfg.n_samples
    seg_labels, _ = exposure.segments

    ev_rows: list[dict[str, Any]] = []
    gu_mat: list[F64] = []
    gross_mat: list[F64] = []
    seg_parts: list[tuple[int, I64, F64, F64]] = []
    loc_aal = {f"{k}_{p}": np.zeros(exposure.n) for k in ("gu", "gross") for p in eng.hazards}

    workers = cfg.workers or min(os.cpu_count() or 1, 8)
    chunks = [list(range(i, len(catalog), _N_CHUNKS)) for i in range(_N_CHUNKS)]
    chunks = [c for c in chunks if c]
    done = 0
    for res in _map_chunks(eng, catalog, chunks, workers):
        for key, arr in res.loc_aal.items():
            loc_aal[key] += arr
        for row, gu, gross, seg in zip(res.rows, res.gu, res.gross, res.segs, strict=True):
            ev_rows.append(row)
            gu_mat.append(gu)
            gross_mat.append(gross)
            seg_parts.append(seg)
        done += res.n_events
        report(0.9 * done / len(catalog), f"events {done}/{len(catalog)}")

    order = np.argsort([r["event_id"] for r in ev_rows], kind="stable")
    ev_rows = [ev_rows[i] for i in order]
    gu_mat = [gu_mat[i] for i in order]
    gross_mat = [gross_mat[i] for i in order]
    seg_parts = [seg_parts[i] for i in order]

    report(0.9, "simulating years")
    n_ev = len(ev_rows)
    gu_m = np.vstack(gu_mat) if n_ev else np.zeros((0, s))
    gross_m = np.vstack(gross_mat) if n_ev else np.zeros((0, s))
    rates = np.array([r["rate"] for r in ev_rows])
    ids = np.array([r["event_id"] for r in ev_rows], dtype=np.int64)
    rng = np.random.default_rng([cfg.seed, 0xC47])
    year, ev_row, smp = simulate_years(rates, s, cfg.n_years, rng)
    occ_gu, occ_gross = gu_m[ev_row, smp], gross_m[ev_row, smp]
    ceded, net = apply_reinsurance(occ_gross, year, cfg.n_years, cfg.reinsurance)

    report(0.95, "computing metrics")
    metrics = {
        name: perspective_metrics(year, loss, cfg.n_years, cfg.return_periods)
        for name, loss in (("gu", occ_gu), ("gross", occ_gross), ("net", net))
    }
    summary = RunSummary(
        n_events=len(catalog),
        n_loss_events=n_ev,
        n_occurrences=int(year.size),
        n_years=cfg.n_years,
        elapsed_s=round(time.perf_counter() - t0, 2),
        aal_elt={
            "gu": float((rates * gu_m.mean(axis=1)).sum()) if n_ev else 0.0,
            "gross": float((rates * gross_m.mean(axis=1)).sum()) if n_ev else 0.0,
        },
        metrics=metrics,
    )

    events = (
        pa.Table.from_pylist(ev_rows)
        if ev_rows
        else pa.table({"event_id": pa.array([], pa.int64())})
    )
    samples = pa.table(
        {
            "event_id": np.repeat(ids, s),
            "sample": np.tile(np.arange(s), n_ev),
            "gu": gu_m.ravel(),
            "gross": gross_m.ravel(),
        }
    )
    seg_cols: dict[str, list[F64 | I64]] = {
        k: [] for k in ("event_id", "sample", "segment", "gu", "gross")
    }
    for eid, segs, sg, sr in seg_parts:
        nz = (sg > 0) | (sr > 0)
        si, sj = np.nonzero(nz)
        seg_cols["event_id"].append(np.full(si.size, eid, dtype=np.int64))
        seg_cols["sample"].append(si.astype(np.int64))
        seg_cols["segment"].append(segs[sj])
        seg_cols["gu"].append(sg[si, sj])
        seg_cols["gross"].append(sr[si, sj])
    segments = pa.table(
        {
            k: pa.array(
                np.concatenate(v)
                if v
                else np.zeros(0, np.int64 if k in ("event_id", "sample", "segment") else np.float64)
            )
            for k, v in seg_cols.items()
        }
    )
    parts = [lbl.split("|") for lbl in seg_labels]
    segment_labels = pa.table(
        {
            "segment": np.arange(len(parts), dtype=np.int64),
            "hub": [p[0] for p in parts],
            "occupancy": np.array([int(p[1]) for p in parts], dtype=np.int64),
            "construction": np.array([int(p[2]) for p in parts], dtype=np.int64),
        }
    )
    ylt = pa.table(
        {
            "year": year,
            "event_id": ids[ev_row] if n_ev else np.zeros(0, np.int64),
            "sample": smp,
            "gu": occ_gu,
            "gross": occ_gross,
            "ceded": ceded,
            "net": net,
        }
    )
    loc_cols: dict[str, Any] = {
        "loc_index": np.arange(exposure.n, dtype=np.int64),
        "LocNumber": pa.array(exposure.loc_number.astype(str)),
    }
    for k in ("gu", "gross"):
        loc_cols[f"aal_{k}"] = sum(loc_aal[f"{k}_{p}"] for p in eng.hazards)
        for p in eng.hazards:
            loc_cols[f"aal_{k}_{p}"] = loc_aal[f"{k}_{p}"]
    loc_aal_table = pa.table(loc_cols)
    report(1.0, "done")
    return RunResult(summary, events, samples, segments, segment_labels, ylt, loc_aal_table)


def peril_names() -> dict[str, str]:
    return dict(oed.PERILS)
