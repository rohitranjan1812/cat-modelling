"""Live monitoring service: poll feeds, nowcast losses on watched portfolios, raise alerts."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from catmod.api.hub import Hub
from catmod.api.store import PortfolioStore
from catmod.engine.drill import FootprintCell, GroupLoss, LocationLoss, LossStats, drill
from catmod.engine.runner import AnalysisConfig, Engine
from catmod.live.feeds import (
    NHC_URL,
    USGS_URL,
    LiveEvent,
    eq_geometry,
    eq_specs,
    fetch_json,
    parse_nhc,
    parse_usgs,
    stable_event_id,
    tc_track,
)

Metric = Literal["mean_gross", "p90_gross", "p99_gross", "mean_gu", "p90_gu", "n_affected"]


class Nowcast(BaseModel):
    portfolio_id: str
    portfolio_name: str
    n_affected: int
    gu: LossStats
    gross: LossStats
    computed_at: datetime
    elapsed_ms: float
    footprint: list[FootprintCell]
    top_locations: list[LocationLoss]
    by_hub: list[GroupLoss]


class LiveEventState(LiveEvent):
    nowcasts: list[Nowcast] = Field(default_factory=list)


class AlertRule(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    name: str = Field(min_length=1, max_length=120)
    peril: str | None = None
    portfolio_id: str | None = None
    metric: Metric = "p90_gross"
    threshold: float = Field(ge=0)
    enabled: bool = True


class Alert(BaseModel):
    id: str
    rule_id: str
    rule_name: str
    live_event_id: str
    event_name: str
    portfolio_id: str
    metric: Metric
    value: float
    threshold: float
    created_at: datetime


class LiveStatus(BaseModel):
    enabled: bool
    interval_s: int
    last_refresh: datetime | None
    last_error: str | None
    sources: dict[str, str]
    n_events: int


class InjectRequest(BaseModel):
    peril: Literal["QEQ", "WTC"]
    name: str = "Simulated event"
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    magnitude: float | None = Field(default=None, ge=4, le=9.6)
    depth_km: float = Field(default=10.0, ge=0, le=700)
    vmax_ms: float | None = Field(default=None, gt=10, le=100)
    heading_deg: float = 300.0
    speed_ms: float = Field(default=5.0, gt=0, le=30)


def _metric(n: Nowcast, m: Metric) -> float:
    if m == "n_affected":
        return float(n.n_affected)
    stat, persp = m.split("_")
    return float(getattr(getattr(n, persp), stat))


class LiveService:
    def __init__(
        self,
        root: Path,
        portfolios: PortfolioStore,
        hub: Hub,
        enabled: bool,
        interval_s: int = 300,
        n_samples: int = 64,
        max_portfolios: int = 3,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.portfolios = portfolios
        self.hub = hub
        self.enabled = enabled
        self.interval_s = interval_s
        self.n_samples = n_samples
        self.max_portfolios = max_portfolios
        self.events: dict[str, LiveEventState] = {}
        self.alerts: list[Alert] = []
        self.rules: dict[str, AlertRule] = {}
        self.last_refresh: datetime | None = None
        self.last_error: str | None = None
        self._lock = threading.RLock()
        self._fired: set[tuple[str, str, str]] = set()
        self._task: asyncio.Task[None] | None = None
        rules = self.root / "rules.json"
        if rules.exists():
            for r in json.loads(rules.read_text()):
                rule = AlertRule.model_validate(r)
                self.rules[rule.id] = rule
        alerts = self.root / "alerts.json"
        if alerts.exists():
            self.alerts = [Alert.model_validate(a) for a in json.loads(alerts.read_text())]
            self._fired = {(a.rule_id, a.live_event_id, a.portfolio_id) for a in self.alerts}

    # ------------------------------------------------------------------ persistence
    def _save_rules(self) -> None:
        (self.root / "rules.json").write_text(
            json.dumps([r.model_dump() for r in self.rules.values()], indent=2)
        )

    def _save_alerts(self) -> None:
        (self.root / "alerts.json").write_text(
            json.dumps([a.model_dump(mode="json") for a in self.alerts[-500:]], indent=2)
        )

    def status(self) -> LiveStatus:
        return LiveStatus(
            enabled=self.enabled,
            interval_s=self.interval_s,
            last_refresh=self.last_refresh,
            last_error=self.last_error,
            sources={"usgs": USGS_URL, "nhc": NHC_URL},
            n_events=len(self.events),
        )

    # ------------------------------------------------------------------ rules
    def upsert_rule(self, rule: AlertRule) -> AlertRule:
        with self._lock:
            self.rules[rule.id] = rule
            self._save_rules()
        # evaluate against current nowcasts immediately
        for ev in list(self.events.values()):
            self._evaluate(ev)
        return rule

    def delete_rule(self, rid: str) -> None:
        with self._lock:
            self.rules.pop(rid)
            self._save_rules()

    # ------------------------------------------------------------------ feeds
    def refresh(self, docs: dict[str, Any] | None = None) -> list[LiveEventState]:
        """Fetch (or accept pre-fetched docs for tests), nowcast new/updated events."""
        found: list[LiveEvent] = []
        errors = []
        for key, url, parser in (("usgs", USGS_URL, parse_usgs), ("nhc", NHC_URL, parse_nhc)):
            try:
                doc = docs[key] if docs is not None else fetch_json(url)
                found.extend(parser(doc))
            except Exception as e:
                errors.append(f"{key}: {e}")
        self.last_error = "; ".join(errors) or None
        self.last_refresh = datetime.now(UTC)
        changed = []
        with self._lock:
            live_ids = {e.id for e in found}
            for eid in [k for k, v in self.events.items() if v.source != "simulated"]:
                if eid not in live_ids:
                    del self.events[eid]
        for ev in found:
            prev = self.events.get(ev.id)
            if prev and prev.model_dump(exclude={"nowcasts"}) == ev.model_dump():
                continue
            changed.append(self._ingest(ev))
        self.hub.publish(
            "live.refresh",
            {"n_events": len(self.events), "changed": len(changed), "error": self.last_error},
        )
        return changed

    def inject(self, req: InjectRequest) -> LiveEventState:
        key = f"sim:{uuid.uuid4().hex[:8]}"
        now = datetime.now(UTC)
        if req.peril == "QEQ":
            mag = req.magnitude or 6.5
            ev = LiveEvent(
                id=key,
                source="simulated",
                peril="QEQ",
                name=req.name,
                time=now,
                lat=req.lat,
                lon=req.lon,
                magnitude=mag,
                depth_km=req.depth_km,
                geometry=eq_geometry(req.lat, req.lon, mag),
                specs=eq_specs(req.lat, req.lon, mag, req.depth_km, req.name),
            )
        else:
            v = req.vmax_ms or 55.0
            track = tc_track(req.lat, req.lon, req.heading_deg, req.speed_ms, v)
            ev = LiveEvent(
                id=key,
                source="simulated",
                peril="WTC",
                name=req.name,
                time=now,
                lat=req.lat,
                lon=req.lon,
                vmax_ms=v,
                classification="SIM",
                geometry={
                    "type": "LineString",
                    "coordinates": [[p["lon"], p["lat"]] for p in track],
                },
                specs=[{"track": track, "name": req.name}],
            )
        return self._ingest(ev)

    def remove(self, event_id: str) -> None:
        with self._lock:
            self.events.pop(event_id)

    # ------------------------------------------------------------------ nowcast
    def _targets(self) -> list[str]:
        return [m.id for m in self.portfolios.all()[: self.max_portfolios]]

    def nowcast(self, ev: LiveEvent, portfolio_id: str) -> Nowcast | None:
        t0 = time.perf_counter()
        x = self.portfolios.exposure(portfolio_id)
        eng = Engine(x, AnalysisConfig(perils=[ev.peril], n_samples=1, workers=1))
        hz = eng.hazards[ev.peril]
        per = max(1, self.n_samples // len(ev.specs))
        members = []
        for k, spec in enumerate(ev.specs):
            e = hz.event_from_spec(spec, stable_event_id(f"{ev.id}#{k}"))
            members.append(eng.event_loss(e, per))
        members = [m for m in members if m.idx.size]
        if not members:
            return None
        # ensemble: pool samples across members; location means averaged over members
        base = max(members, key=lambda m: m.idx.size)
        d = drill(x, base, hz.unit, top=25)
        gu = np.concatenate([m.gu for m in members])
        gross = np.concatenate([m.gross for m in members])
        return Nowcast(
            portfolio_id=portfolio_id,
            portfolio_name=self.portfolios.get(portfolio_id).name,
            n_affected=max(m.idx.size for m in members),
            gu=LossStats.of(gu),
            gross=LossStats.of(gross),
            computed_at=datetime.now(UTC),
            elapsed_ms=round((time.perf_counter() - t0) * 1e3, 1),
            footprint=d.footprint,
            top_locations=d.top_locations,
            by_hub=d.by_hub,
        )

    def _ingest(self, ev: LiveEvent) -> LiveEventState:
        nowcasts = []
        for pid in self._targets():
            try:
                n = self.nowcast(ev, pid)
            except Exception as e:  # keep the feed alive if one portfolio fails
                self.last_error = f"nowcast {ev.id}/{pid}: {e}"
                continue
            if n is not None:
                nowcasts.append(n)
        state = LiveEventState(**ev.model_dump(), nowcasts=nowcasts)
        with self._lock:
            self.events[ev.id] = state
        self.hub.publish("live.event", state.model_dump(mode="json", exclude={"specs"}))
        self._evaluate(state)
        return state

    def _evaluate(self, ev: LiveEventState) -> list[Alert]:
        fired = []
        for rule in list(self.rules.values()):
            if not rule.enabled or (rule.peril and rule.peril != ev.peril):
                continue
            for n in ev.nowcasts:
                if rule.portfolio_id and rule.portfolio_id != n.portfolio_id:
                    continue
                key = (rule.id, ev.id, n.portfolio_id)
                val = _metric(n, rule.metric)
                if val < rule.threshold or key in self._fired:
                    continue
                alert = Alert(
                    id=uuid.uuid4().hex[:10],
                    rule_id=rule.id,
                    rule_name=rule.name,
                    live_event_id=ev.id,
                    event_name=ev.name,
                    portfolio_id=n.portfolio_id,
                    metric=rule.metric,
                    value=val,
                    threshold=rule.threshold,
                    created_at=datetime.now(UTC),
                )
                with self._lock:
                    self._fired.add(key)
                    self.alerts.append(alert)
                    self._save_alerts()
                self.hub.publish("alert", alert.model_dump(mode="json"))
                fired.append(alert)
        return fired

    # ------------------------------------------------------------------ polling
    async def _loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                await loop.run_in_executor(None, self.refresh)
            except Exception as e:  # pragma: no cover
                self.last_error = str(e)
            await asyncio.sleep(self.interval_s)

    def start(self) -> None:
        if self.enabled and self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
