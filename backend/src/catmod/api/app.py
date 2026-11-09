"""FastAPI application. The OpenAPI document is the contract for the web UI and SDKs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

import pyarrow as pa
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from catmod import __version__, oed
from catmod.api.store import AggregateRow, Dimension, NotFoundError, PortfolioMeta, PortfolioStore
from catmod.config import PortfolioConfig, list_presets, load_preset

ARROW_STREAM = "application/vnd.apache.arrow.stream"


class Health(BaseModel):
    status: str = "ok"
    version: str = __version__


class CodeLists(BaseModel):
    perils: dict[str, str]
    occupancy: dict[int, str]
    construction: dict[int, str]
    ded_type: dict[int, str]


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


def get_store(request: Request) -> PortfolioStore:
    store: PortfolioStore = request.app.state.store
    return store


StoreDep = Annotated[PortfolioStore, Depends(get_store)]


def create_app(data_dir: Path | None = None) -> FastAPI:
    root = data_dir or Path(os.environ.get("CATMOD_DATA_DIR", "./data/portfolios"))
    store = PortfolioStore(root)

    app = FastAPI(
        title="catmod API",
        version=__version__,
        description="Open-source interactive catastrophe modelling platform.",
        separate_input_output_schemas=False,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("CATMOD_CORS_ORIGINS", "http://localhost:5173").split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.store = store

    def meta_or_404(store: PortfolioStore, pid: str) -> PortfolioMeta:
        try:
            return store.get(pid)
        except NotFoundError:
            raise HTTPException(404, f"portfolio {pid!r} not found") from None

    @app.get("/health", response_model=Health, tags=["system"])
    def health() -> Health:
        return Health()

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

    @app.post(
        "/api/v1/portfolios", response_model=PortfolioMeta, status_code=201, tags=["portfolios"]
    )
    def create_portfolio(body: CreatePortfolio, store: StoreDep) -> PortfolioMeta:
        try:
            cfg = body.resolve()
        except KeyError as e:
            raise HTTPException(404, str(e)) from None
        return store.create(cfg)

    @app.get("/api/v1/portfolios", response_model=list[PortfolioMeta], tags=["portfolios"])
    def list_portfolios(store: StoreDep) -> list[PortfolioMeta]:
        return store.all()

    @app.get("/api/v1/portfolios/{pid}", response_model=PortfolioMeta, tags=["portfolios"])
    def get_portfolio(pid: str, store: StoreDep) -> PortfolioMeta:
        return meta_or_404(store, pid)

    @app.delete("/api/v1/portfolios/{pid}", status_code=204, tags=["portfolios"])
    def delete_portfolio(pid: str, store: StoreDep) -> Response:
        meta_or_404(store, pid)
        store.delete(pid)
        return Response(status_code=204)

    @app.get(
        "/api/v1/portfolios/{pid}/aggregate",
        response_model=list[AggregateRow],
        tags=["portfolios"],
    )
    def aggregate(
        pid: str,
        store: StoreDep,
        by: Dimension = "hub",
        h3_res: Annotated[int | None, Query(ge=0, le=15)] = None,
    ) -> list[AggregateRow]:
        meta_or_404(store, pid)
        try:
            return store.aggregate(pid, by, h3_res)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @app.get(
        "/api/v1/portfolios/{pid}/locations.arrow",
        tags=["portfolios"],
        response_class=Response,
        responses={200: {"content": {ARROW_STREAM: {}}, "description": "Arrow IPC stream"}},
    )
    def locations_arrow(pid: str, store: StoreDep) -> Response:
        meta_or_404(store, pid)
        table = store.map_table(pid)
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, table.schema) as writer:
            writer.write_table(table)
        return Response(sink.getvalue().to_pybytes(), media_type=ARROW_STREAM)

    @app.get(
        "/api/v1/portfolios/{pid}/files/{table}.parquet",
        tags=["portfolios"],
        response_class=FileResponse,
        responses={200: {"content": {"application/vnd.apache.parquet": {}}}},
    )
    def parquet_file(pid: str, table: str, store: StoreDep) -> FileResponse:
        meta_or_404(store, pid)
        paths = {"location": store.location_path, "account": store.account_path}
        if table not in paths:
            raise HTTPException(404, "table must be 'location' or 'account'")
        return FileResponse(paths[table](pid), media_type="application/vnd.apache.parquet")

    return app
