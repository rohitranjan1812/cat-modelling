"""FastAPI application. The OpenAPI document is the contract for the web UI and SDKs.

Data layout under ``CATMOD_DATA_DIR`` (default ./data):
    portfolios/<id>/   location/account Parquet + manifest
    runs/<id>/         ELT, samples, segments, YLT, location AAL + manifest
    live/              alert rules and fired alerts
    dashboards/        declarative dashboard specs
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any, Literal

import pyarrow as pa
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from catmod import __version__, oed
from catmod.api.extras import (
    Dashboard,
    DashboardIn,
    DashboardStore,
    ScenarioRequest,
    ScenarioResult,
    default_widgets,
    run_scenario,
)
from catmod.api.hub import Hub
from catmod.api.runs import (
    BreakdownRow,
    EPResponse,
    EventRow,
    EventsAtRP,
    Filters,
    Perspective,
    RunMeta,
    RunStore,
)
from catmod.api.store import AggregateRow, Dimension, NotFoundError, PortfolioMeta, PortfolioStore
from catmod.config import PortfolioConfig, list_presets, load_preset
from catmod.engine.drill import EventDrill, analysis_presets
from catmod.engine.registry import describe
from catmod.engine.runner import AnalysisConfig
from catmod.live.service import (
    Alert,
    AlertRule,
    InjectRequest,
    LiveEventState,
    LiveService,
    LiveStatus,
)

ARROW_STREAM = "application/vnd.apache.arrow.stream"


class Health(BaseModel):
    status: str = "ok"
    version: str = __version__


class CodeLists(BaseModel):
    perils: dict[str, str]
    occupancy: dict[int, str]
    construction: dict[int, str]
    ded_type: dict[int, str]


class Capabilities(BaseModel):
    version: str
    spatial_fields: list[str]
    copulas: list[str]
    hazards: list[str]
    analysis_presets: list[str]
    portfolio_presets: list[str]
    live_enabled: bool
    auth: bool


class CreatePortfolio(BaseModel):
    """Generate from a preset (optionally overriding size/seed) or from a full config."""

    preset: str | None = None
    config: PortfolioConfig | None = None
    n_locations: int | None = Field(default=None, ge=1, le=5_000_000)
    seed: int | None = Field(default=None, ge=0)
    name: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def _one_source(self) -> CreatePortfolio:
        if (self.preset is None) == (self.config is None):
            raise ValueError("provide exactly one of 'preset' or 'config'")
        return self

    def resolve(self) -> PortfolioConfig:
        base = self.config if self.config is not None else load_preset(str(self.preset))
        overrides: dict[str, Any] = {
            k: v
            for k, v in {
                "n_locations": self.n_locations,
                "seed": self.seed,
                "name": self.name,
            }.items()
            if v is not None
        }
        return PortfolioConfig.model_validate({**base.model_dump(), **overrides})


class CreateRun(BaseModel):
    """Start an analysis from an analysis preset (with overrides) or a full config."""

    portfolio_id: str
    preset: str | None = "standard"
    config: AnalysisConfig | None = None
    overrides: dict[str, Any] = Field(
        default_factory=dict, description="Shallow overrides applied on top of the preset"
    )

    def resolve(self) -> AnalysisConfig:
        if self.config is not None:
            return self.config
        presets = analysis_presets()
        if self.preset not in presets:
            raise KeyError(f"unknown analysis preset {self.preset!r}; have {sorted(presets)}")
        base = presets[str(self.preset)].model_dump()
        return AnalysisConfig.model_validate({**base, **self.overrides})


class Page(BaseModel):
    total: int
    items: list[EventRow]


# ---------------------------------------------------------------------------- dependencies
def _state(request: Request) -> Any:
    return request.app.state


def get_store(request: Request) -> PortfolioStore:
    store: PortfolioStore = request.app.state.store
    return store


def get_runs(request: Request) -> RunStore:
    runs: RunStore = request.app.state.runs
    return runs


def get_live(request: Request) -> LiveService:
    live: LiveService = request.app.state.live
    return live


def get_dashboards(request: Request) -> DashboardStore:
    d: DashboardStore = request.app.state.dashboards
    return d


StoreDep = Annotated[PortfolioStore, Depends(get_store)]
RunsDep = Annotated[RunStore, Depends(get_runs)]
LiveDep = Annotated[LiveService, Depends(get_live)]
DashDep = Annotated[DashboardStore, Depends(get_dashboards)]


def filters_dep(
    peril: Annotated[list[str] | None, Query()] = None,
    hub: Annotated[list[str] | None, Query()] = None,
    occupancy: Annotated[list[int] | None, Query()] = None,
    construction: Annotated[list[int] | None, Query()] = None,
) -> Filters:
    return Filters(
        perils=peril or [],
        hubs=hub or [],
        occupancies=occupancy or [],
        constructions=construction or [],
    )


FiltersDep = Annotated[Filters, Depends(filters_dep)]


def _arrow(table: pa.Table) -> Response:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return Response(sink.getvalue().to_pybytes(), media_type=ARROW_STREAM)


def create_app(data_dir: Path | None = None, live_enabled: bool | None = None) -> FastAPI:
    root = data_dir or Path(os.environ.get("CATMOD_DATA_DIR", "./data"))
    api_key = os.environ.get("CATMOD_API_KEY") or None
    hub = Hub()
    store = PortfolioStore(root / "portfolios")
    runs = RunStore(root / "runs", store, hub)
    live = LiveService(
        root / "live",
        store,
        hub,
        enabled=live_enabled
        if live_enabled is not None
        else os.environ.get("CATMOD_LIVE", "0") == "1",
        interval_s=int(os.environ.get("CATMOD_LIVE_INTERVAL", "300")),
    )
    dashboards = DashboardStore(root / "dashboards")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        hub.bind(asyncio.get_running_loop())
        live.start()
        yield
        await live.stop()

    app = FastAPI(
        title="catmod API",
        version=__version__,
        description="Open-source interactive catastrophe modelling platform.",
        separate_input_output_schemas=False,
        lifespan=lifespan,
    )
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("CATMOD_CORS_ORIGINS", "http://localhost:5173").split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.store, app.state.runs, app.state.live = store, runs, live
    app.state.dashboards, app.state.hub = dashboards, hub

    @app.middleware("http")
    async def auth(request: Request, call_next: Any) -> Response:
        if api_key and request.url.path.startswith("/api/"):
            supplied = request.headers.get("x-api-key") or request.query_params.get("api_key")
            if not supplied or not hmac.compare_digest(supplied, api_key):
                return JSONResponse({"detail": "invalid or missing API key"}, status_code=401)
        response: Response = await call_next(request)
        return response

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, e: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": f"not found: {e.args[0] if e.args else ''}"}, 404)

    @app.exception_handler(ValueError)
    async def bad_value(_: Request, e: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, 422)

    # ------------------------------------------------------------------ system / reference
    @app.get("/health", response_model=Health, tags=["system"])
    def health() -> Health:
        return Health()

    @app.get("/api/v1/capabilities", response_model=Capabilities, tags=["system"])
    def capabilities() -> Capabilities:
        reg = describe()
        return Capabilities(
            version=__version__,
            **reg,
            analysis_presets=sorted(analysis_presets()),
            portfolio_presets=list_presets(),
            live_enabled=live.enabled,
            auth=api_key is not None,
        )

    @app.get("/api/v1/codes", response_model=CodeLists, tags=["reference"])
    def codes() -> CodeLists:
        return CodeLists(
            perils=oed.PERILS,
            occupancy=oed.OCCUPANCY,
            construction=oed.CONSTRUCTION,
            ded_type=oed.DED_TYPE,
        )

    @app.get("/api/v1/presets", response_model=list[str], tags=["reference"])
    def presets() -> list[str]:
        return list_presets()

    @app.get("/api/v1/presets/{name}", response_model=PortfolioConfig, tags=["reference"])
    def preset(name: str) -> PortfolioConfig:
        try:
            return load_preset(name)
        except KeyError as e:
            raise HTTPException(404, str(e)) from None

    @app.get(
        "/api/v1/analysis/presets", response_model=dict[str, AnalysisConfig], tags=["reference"]
    )
    def analysis_preset_list() -> dict[str, AnalysisConfig]:
        return analysis_presets()

    # ------------------------------------------------------------------ portfolios
    @app.post(
        "/api/v1/portfolios", response_model=PortfolioMeta, status_code=201, tags=["portfolios"]
    )
    def create_portfolio(body: CreatePortfolio, store: StoreDep) -> PortfolioMeta:
        try:
            cfg = body.resolve()
        except KeyError as e:
            raise HTTPException(404, str(e)) from None
        meta = store.create(cfg)
        hub.publish("portfolio.created", {"id": meta.id, "name": meta.name})
        return meta

    @app.get("/api/v1/portfolios", response_model=list[PortfolioMeta], tags=["portfolios"])
    def list_portfolios(store: StoreDep) -> list[PortfolioMeta]:
        return store.all()

    @app.get("/api/v1/portfolios/{pid}", response_model=PortfolioMeta, tags=["portfolios"])
    def get_portfolio(pid: str, store: StoreDep) -> PortfolioMeta:
        return store.get(pid)

    @app.delete("/api/v1/portfolios/{pid}", status_code=204, tags=["portfolios"])
    def delete_portfolio(pid: str, store: StoreDep, runs: RunsDep) -> Response:
        store.get(pid)
        for r in runs.all(pid):
            runs.delete(r.id)
        store.delete(pid)
        return Response(status_code=204)

    @app.get(
        "/api/v1/portfolios/{pid}/aggregate", response_model=list[AggregateRow], tags=["portfolios"]
    )
    def aggregate(
        pid: str,
        store: StoreDep,
        by: Dimension = "hub",
        h3_res: Annotated[int | None, Query(ge=0, le=15)] = None,
    ) -> list[AggregateRow]:
        return store.aggregate(pid, by, h3_res)

    @app.get(
        "/api/v1/portfolios/{pid}/locations.arrow",
        tags=["portfolios"],
        response_class=Response,
        responses={200: {"content": {ARROW_STREAM: {}}, "description": "Arrow IPC stream"}},
    )
    def locations_arrow(pid: str, store: StoreDep) -> Response:
        store.get(pid)
        return _arrow(store.map_table(pid))

    @app.get(
        "/api/v1/portfolios/{pid}/files/{table}.parquet",
        tags=["portfolios"],
        response_class=FileResponse,
        responses={200: {"content": {"application/vnd.apache.parquet": {}}}},
    )
    def parquet_file(pid: str, table: str, store: StoreDep) -> FileResponse:
        paths = {"location": store.location_path, "account": store.account_path}
        if table not in paths:
            raise HTTPException(404, "table must be 'location' or 'account'")
        return FileResponse(paths[table](pid), media_type="application/vnd.apache.parquet")

    # ------------------------------------------------------------------ runs
    @app.post("/api/v1/runs", response_model=RunMeta, status_code=202, tags=["runs"])
    def create_run(
        body: CreateRun,
        runs: RunsDep,
        wait: Annotated[bool, Query(description="Block until finished")] = False,
    ) -> RunMeta:
        try:
            cfg = body.resolve()
        except KeyError as e:
            raise HTTPException(404, str(e)) from None
        return runs.submit(body.portfolio_id, cfg, wait=wait)

    @app.get("/api/v1/runs", response_model=list[RunMeta], tags=["runs"])
    def list_runs(runs: RunsDep, portfolio_id: str | None = None) -> list[RunMeta]:
        return runs.all(portfolio_id)

    @app.get("/api/v1/runs/{rid}", response_model=RunMeta, tags=["runs"])
    def get_run(rid: str, runs: RunsDep) -> RunMeta:
        return runs.get(rid)

    @app.delete("/api/v1/runs/{rid}", status_code=204, tags=["runs"])
    def delete_run(rid: str, runs: RunsDep) -> Response:
        runs.delete(rid)
        return Response(status_code=204)

    @app.get("/api/v1/runs/{rid}/ep", response_model=EPResponse, tags=["runs"])
    def run_ep(
        rid: str, runs: RunsDep, f: FiltersDep, perspective: Perspective = "gross"
    ) -> EPResponse:
        return runs.ep(rid, perspective, f)

    @app.get("/api/v1/runs/{rid}/breakdown", response_model=list[BreakdownRow], tags=["runs"])
    def run_breakdown(
        rid: str,
        runs: RunsDep,
        f: FiltersDep,
        by: Literal["hub", "occupancy", "construction", "country", "peril", "h3"] = "hub",
        h3_res: Annotated[int | None, Query(ge=0, le=15)] = None,
    ) -> list[BreakdownRow]:
        return runs.breakdown(rid, by, f, h3_res)

    @app.get("/api/v1/runs/{rid}/events", response_model=Page, tags=["runs"])
    def run_events(
        rid: str,
        runs: RunsDep,
        peril: str | None = None,
        sort: str = "mean_gross",
        limit: Annotated[int, Query(ge=1, le=1000)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        min_loss: float = 0.0,
    ) -> Page:
        total, items = runs.events(rid, peril, sort, limit, offset, min_loss)
        return Page(total=total, items=items)

    @app.get("/api/v1/runs/{rid}/events-at-rp", response_model=EventsAtRP, tags=["runs"])
    def run_events_at_rp(
        rid: str,
        runs: RunsDep,
        rp: Annotated[float, Query(gt=1)],
        curve: Literal["oep", "aep"] = "oep",
        perspective: Perspective = "gross",
        window: Annotated[float, Query(gt=0, le=1)] = 0.1,
        limit: Annotated[int, Query(ge=1, le=200)] = 25,
    ) -> EventsAtRP:
        return runs.events_at_rp(rid, rp, curve, perspective, window, limit)

    @app.get("/api/v1/runs/{rid}/events/{event_id}", response_model=EventDrill, tags=["runs"])
    def run_event(
        rid: str, event_id: int, runs: RunsDep, top: Annotated[int, Query(ge=1, le=2000)] = 100
    ) -> EventDrill:
        try:
            return runs.event_detail(rid, event_id, top)
        except KeyError:
            raise HTTPException(404, f"event {event_id} not in catalogue") from None

    @app.get(
        "/api/v1/runs/{rid}/locations.arrow",
        tags=["runs"],
        response_class=Response,
        responses={200: {"content": {ARROW_STREAM: {}}, "description": "Arrow IPC stream"}},
    )
    def run_locations(rid: str, runs: RunsDep) -> Response:
        return _arrow(runs.locations_arrow(rid))

    @app.get(
        "/api/v1/runs/{rid}/files/{name}.parquet",
        tags=["runs"],
        response_class=FileResponse,
        responses={200: {"content": {"application/vnd.apache.parquet": {}}}},
    )
    def run_file(rid: str, name: str, runs: RunsDep) -> FileResponse:
        if name not in {"events", "samples", "segments", "segment_labels", "ylt", "loc_aal"}:
            raise HTTPException(404, f"no table {name!r}")
        return FileResponse(
            runs.path(rid, name),
            media_type="application/vnd.apache.parquet",
            filename=f"{rid}-{name}.parquet",
        )

    # ------------------------------------------------------------------ scenario lab
    @app.post("/api/v1/scenarios", response_model=ScenarioResult, tags=["scenarios"])
    async def scenario(body: ScenarioRequest, store: StoreDep) -> ScenarioResult:
        return await asyncio.get_running_loop().run_in_executor(None, run_scenario, store, body)

    # ------------------------------------------------------------------ live
    @app.get("/api/v1/live/status", response_model=LiveStatus, tags=["live"])
    def live_status(live: LiveDep) -> LiveStatus:
        return live.status()

    @app.get("/api/v1/live/events", response_model=list[LiveEventState], tags=["live"])
    def live_events(live: LiveDep) -> list[LiveEventState]:
        return sorted(live.events.values(), key=lambda e: e.time, reverse=True)

    @app.post("/api/v1/live/refresh", response_model=list[LiveEventState], tags=["live"])
    async def live_refresh(live: LiveDep) -> list[LiveEventState]:
        return await asyncio.get_running_loop().run_in_executor(None, live.refresh)

    @app.post("/api/v1/live/inject", response_model=LiveEventState, status_code=201, tags=["live"])
    async def live_inject(body: InjectRequest, live: LiveDep) -> LiveEventState:
        return await asyncio.get_running_loop().run_in_executor(None, live.inject, body)

    @app.delete("/api/v1/live/events/{event_id}", status_code=204, tags=["live"])
    def live_remove(event_id: str, live: LiveDep) -> Response:
        if event_id not in live.events:
            raise HTTPException(404, "no such live event")
        live.remove(event_id)
        return Response(status_code=204)

    @app.get("/api/v1/alerts", response_model=list[Alert], tags=["live"])
    def alerts(live: LiveDep) -> list[Alert]:
        return list(reversed(live.alerts))

    @app.get("/api/v1/alerts/rules", response_model=list[AlertRule], tags=["live"])
    def alert_rules(live: LiveDep) -> list[AlertRule]:
        return list(live.rules.values())

    @app.post("/api/v1/alerts/rules", response_model=AlertRule, status_code=201, tags=["live"])
    def upsert_rule(rule: AlertRule, live: LiveDep) -> AlertRule:
        return live.upsert_rule(rule)

    @app.delete("/api/v1/alerts/rules/{rule_id}", status_code=204, tags=["live"])
    def delete_rule(rule_id: str, live: LiveDep) -> Response:
        if rule_id not in live.rules:
            raise HTTPException(404, "no such rule")
        live.delete_rule(rule_id)
        return Response(status_code=204)

    # ------------------------------------------------------------------ dashboards
    @app.get("/api/v1/dashboards", response_model=list[Dashboard], tags=["dashboards"])
    def dashboards_list(d: DashDep) -> list[Dashboard]:
        return d.all()

    @app.get("/api/v1/dashboards/template", response_model=DashboardIn, tags=["dashboards"])
    def dashboard_template() -> DashboardIn:
        return DashboardIn(name="Risk overview", widgets=default_widgets())

    @app.post("/api/v1/dashboards", response_model=Dashboard, status_code=201, tags=["dashboards"])
    def dashboard_create(body: DashboardIn, d: DashDep) -> Dashboard:
        return d.save(body)

    @app.get("/api/v1/dashboards/{did}", response_model=Dashboard, tags=["dashboards"])
    def dashboard_get(did: str, d: DashDep) -> Dashboard:
        return d.get(did)

    @app.put("/api/v1/dashboards/{did}", response_model=Dashboard, tags=["dashboards"])
    def dashboard_put(did: str, body: DashboardIn, d: DashDep) -> Dashboard:
        d.get(did)
        return d.save(body, did)

    @app.delete("/api/v1/dashboards/{did}", status_code=204, tags=["dashboards"])
    def dashboard_delete(did: str, d: DashDep) -> Response:
        d.delete(did)
        return Response(status_code=204)

    # ------------------------------------------------------------------ websocket
    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        if api_key:
            supplied = socket.query_params.get("api_key") or ""
            if not hmac.compare_digest(supplied, api_key):
                await socket.close(code=4401)
                return
        await hub.connect(socket)
        try:
            while True:
                await socket.receive_text()  # keep-alive / ignore client messages
        except WebSocketDisconnect:
            hub.disconnect(socket)

    # ------------------------------------------------------------------ optional SPA
    web_dist = os.environ.get("CATMOD_WEB_DIST")
    if web_dist and Path(web_dist).is_dir():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")

    return app
