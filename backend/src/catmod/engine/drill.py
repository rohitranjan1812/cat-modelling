"""Event-level drill-down products shared by run explorer, scenario lab and live nowcast."""

from __future__ import annotations

from typing import Any

import numpy as np
from pydantic import BaseModel

from catmod.engine.context import Exposure
from catmod.engine.eq import EqConfig
from catmod.engine.runner import AnalysisConfig, EventLoss
from catmod.engine.tc import TcConfig
from catmod.geo import h3_cells


class LossStats(BaseModel):
    mean: float
    sd: float
    p10: float
    p50: float
    p90: float
    p99: float
    max: float

    @classmethod
    def of(cls, x: np.ndarray) -> LossStats:
        if x.size == 0:
            return cls(mean=0, sd=0, p10=0, p50=0, p90=0, p99=0, max=0)
        q = np.quantile(x, [0.1, 0.5, 0.9, 0.99])
        return cls(
            mean=float(x.mean()),
            sd=float(x.std()),
            p10=float(q[0]),
            p50=float(q[1]),
            p90=float(q[2]),
            p99=float(q[3]),
            max=float(x.max()),
        )


class FootprintCell(BaseModel):
    h3: str
    n: int
    intensity: float  # mean of median intensity in the cell
    gu: float  # mean GU (over samples) summed in the cell
    gross: float


class LocationLoss(BaseModel):
    loc_number: str
    acc_number: str
    lat: float
    lon: float
    hub: str
    occupancy: int
    construction: int
    year_built: int
    tiv: float
    intensity: float
    gu: float
    gross: float
    loc_ded: float
    loc_ded_type: int
    loc_limit: float
    layer_attachment: float
    layer_limit: float
    layer_participation: float


class GroupLoss(BaseModel):
    key: str
    n: int
    gu: float
    gross: float


class EventDrill(BaseModel):
    event_id: int
    peril: str
    name: str
    rate: float
    unit: str
    geometry: dict[str, Any]
    summary: dict[str, Any]
    n_affected: int
    gu: LossStats
    gross: LossStats
    samples_gu: list[float]
    samples_gross: list[float]
    footprint: list[FootprintCell]
    top_locations: list[LocationLoss]
    by_hub: list[GroupLoss]


def drill(x: Exposure, el: EventLoss, unit: str, top: int = 100, h3_res: int = 6) -> EventDrill:
    idx = el.idx
    ev = el.event
    cells: list[FootprintCell] = []
    tops: list[LocationLoss] = []
    by_hub: list[GroupLoss] = []
    if idx.size:
        inten = np.exp(el.footprint.ln_median)
        gu_m, gr_m = el.loc_gu.mean(axis=0), el.loc_gross.mean(axis=0)
        cell = h3_cells(x.lat[idx], x.lon[idx], h3_res)
        uc, inv = np.unique(cell, return_inverse=True)
        cnt = np.bincount(inv)
        cells = [
            FootprintCell(
                h3=format(int(c), "x"), n=int(n), intensity=float(i), gu=float(g), gross=float(r)
            )
            for c, n, i, g, r in zip(
                uc,
                cnt,
                np.bincount(inv, inten) / cnt,
                np.bincount(inv, gu_m),
                np.bincount(inv, gr_m),
                strict=True,
            )
        ]
        order = np.argsort(-gr_m - 1e-9 * gu_m)[:top]
        tiv = x.tiv
        for o in order:
            i = int(idx[o])
            a = int(x.acc_idx[i])
            tops.append(
                LocationLoss(
                    loc_number=str(x.loc_number[i]),
                    acc_number=str(x.acc_number[a]),
                    lat=float(x.lat[i]),
                    lon=float(x.lon[i]),
                    hub=str(x.hub[i]),
                    occupancy=int(x.occupancy[i]),
                    construction=int(x.construction[i]),
                    year_built=int(x.year_built[i]),
                    tiv=float(tiv[i]),
                    intensity=float(inten[o]),
                    gu=float(gu_m[o]),
                    gross=float(gr_m[o]),
                    loc_ded=float(x.ded[i]),
                    loc_ded_type=int(x.ded_type[i]),
                    loc_limit=float(x.limit[i]),
                    layer_attachment=float(x.acc_attachment[a]),
                    layer_limit=float(x.acc_limit[a]),
                    layer_participation=float(x.acc_participation[a]),
                )
            )
        hubs, hinv = np.unique(x.hub[idx].astype(str), return_inverse=True)
        by_hub = sorted(
            (
                GroupLoss(key=str(h), n=int(n), gu=float(g), gross=float(r))
                for h, n, g, r in zip(
                    hubs,
                    np.bincount(hinv),
                    np.bincount(hinv, gu_m),
                    np.bincount(hinv, gr_m),
                    strict=True,
                )
            ),
            key=lambda gl: -gl.gu,
        )
    return EventDrill(
        event_id=ev.event_id,
        peril=ev.peril,
        name=ev.name,
        rate=ev.rate,
        unit=unit,
        geometry=ev.geometry(),
        summary=ev.summary(),
        n_affected=int(idx.size),
        gu=LossStats.of(el.gu),
        gross=LossStats.of(el.gross),
        samples_gu=el.gu.tolist(),
        samples_gross=el.gross.tolist(),
        footprint=cells,
        top_locations=tops,
        by_hub=by_hub,
    )


def analysis_presets() -> dict[str, AnalysisConfig]:
    quick_tc = TcConfig(
        gate_spacing_km=200,
        vmax_step=10,
        heading_offsets_deg=[-25.0, 25.0],
        heading_weights=[0.5, 0.5],
        rmax_factors=[1.0],
        rmax_weights=[1.0],
    )
    return {
        "standard": AnalysisConfig(name="Standard (50k years)"),
        "quick": AnalysisConfig(
            name="Quick look (10k years)",
            n_samples=4,
            n_years=10_000,
            eq=EqConfig(dm=0.4, position_step_km=40, area_sites=6),
            tc=quick_tc,
        ),
        "high-fidelity": AnalysisConfig(
            name="High fidelity (32 samples, 100k years)", n_samples=32, n_years=100_000
        ),
    }
