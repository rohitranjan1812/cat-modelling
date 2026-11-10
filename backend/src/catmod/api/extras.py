"""Scenario lab and declarative dashboards."""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from catmod.api.store import NotFoundError, PortfolioStore
from catmod.engine.drill import EventDrill, drill
from catmod.engine.eq import EqConfig
from catmod.engine.runner import AnalysisConfig, DamageCorrelationConfig, Engine
from catmod.engine.tc import TcConfig
from catmod.engine.vulnerability import VulnerabilityConfig

SCENARIO_EVENT_ID = 9_000_001


class ScenarioRequest(BaseModel):
    """A single deterministic event against a portfolio, with full secondary uncertainty.

    ``event`` for QEQ: {lat, lon, magnitude, depth_km?, strike_deg?}
    ``event`` for WTC: {lat, lon, vmax_ms, heading_deg?, rmax_km?, translation_ms?} (landfall)
                       or {track: [{lat, lon, vmax_ms, rmax_km?, t_h?}, ...]}
    """

    portfolio_id: str
    peril: Literal["QEQ", "WTC"]
    event: dict[str, Any]
    n_samples: int = Field(default=64, ge=1, le=1000)
    seed: int = Field(default=7, ge=0)
    damage_correlation: DamageCorrelationConfig = DamageCorrelationConfig()
    vulnerability: VulnerabilityConfig | None = None
    eq: EqConfig | None = None
    tc: TcConfig | None = None
    top: int = Field(default=100, ge=1, le=2000)


class ScenarioResult(EventDrill):
    elapsed_ms: float


def run_scenario(store: PortfolioStore, req: ScenarioRequest) -> ScenarioResult:
    t0 = time.perf_counter()
    x = store.exposure(req.portfolio_id)
    cfg = AnalysisConfig(
        perils=[req.peril],
        n_samples=req.n_samples,
        seed=req.seed,
        workers=1,
        damage_correlation=req.damage_correlation,
        vulnerability=req.vulnerability or VulnerabilityConfig(),
        eq=req.eq or EqConfig(),
        tc=req.tc or TcConfig(),
    )
    eng = Engine(x, cfg)
    hz = eng.hazards[req.peril]
    ev = hz.event_from_spec(req.event, SCENARIO_EVENT_ID)
    el = eng.event_loss(ev, req.n_samples)
    d = drill(x, el, hz.unit, top=req.top)
    return ScenarioResult(**d.model_dump(), elapsed_ms=round((time.perf_counter() - t0) * 1e3, 1))


# ---------------------------------------------------------------------------- dashboards
WidgetType = Literal[
    "kpi", "ep", "breakdown", "treemap", "heatmap", "events", "map", "metrics", "histogram"
]


class Widget(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    type: WidgetType
    title: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    x: int = Field(default=0, ge=0, le=11)
    y: int = Field(default=0, ge=0)
    w: int = Field(default=6, ge=1, le=12)
    h: int = Field(default=4, ge=1, le=20)


class DashboardIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    run_id: str | None = None
    widgets: list[Widget] = Field(default_factory=list)


class Dashboard(DashboardIn):
    id: str
    updated_at: datetime


def default_widgets() -> list[Widget]:
    return [
        Widget(type="kpi", title="Headline", params={"perspective": "gross"}, x=0, y=0, w=12, h=2),
        Widget(
            type="ep",
            title="Exceedance curves",
            params={"perspective": "gross"},
            x=0,
            y=2,
            w=6,
            h=5,
        ),
        Widget(type="map", title="AAL map", params={"metric": "aal_gross"}, x=6, y=2, w=6, h=5),
        Widget(type="breakdown", title="AAL by hub", params={"by": "hub"}, x=0, y=7, w=4, h=4),
        Widget(type="treemap", title="AAL: peril > hub > occupancy", x=4, y=7, w=4, h=4),
        Widget(
            type="heatmap",
            title="Hub x construction",
            params={"rows": "hub", "cols": "construction"},
            x=8,
            y=7,
            w=4,
            h=4,
        ),
        Widget(type="events", title="Largest events", params={"limit": 15}, x=0, y=11, w=12, h=5),
    ]


class DashboardStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, did: str) -> Path:
        if not did.isalnum():
            raise NotFoundError(did)
        return self.root / f"{did}.json"

    def all(self) -> list[Dashboard]:
        items = [
            Dashboard.model_validate(json.loads(p.read_text())) for p in self.root.glob("*.json")
        ]
        return sorted(items, key=lambda d: d.updated_at, reverse=True)

    def get(self, did: str) -> Dashboard:
        p = self._path(did)
        if not p.exists():
            raise NotFoundError(did)
        return Dashboard.model_validate_json(p.read_text())

    def save(self, body: DashboardIn, did: str | None = None) -> Dashboard:
        d = Dashboard(
            **body.model_dump(), id=did or uuid.uuid4().hex[:10], updated_at=datetime.now(UTC)
        )
        with self._lock:
            self._path(d.id).write_text(d.model_dump_json(indent=2))
        return d

    def delete(self, did: str) -> None:
        self.get(did)
        self._path(did).unlink()
