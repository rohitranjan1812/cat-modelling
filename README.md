# catmod

**Open-source, interactive catastrophe modelling.** A transparent hazard → exposure → vulnerability → financial
engine with sub-second what-if simulation, live event nowcasting, and linked map/widget drill-down.

> Status: **Phase 1 — foundations** (exposure schema, synthetic portfolios, API contract, web shell).
> See the [roadmap](docs/roadmap.md).

## Why

[Oasis LMF](https://oasislmf.org) is the open standard for batch loss computation. catmod targets what it doesn't:
an *explorable* platform — change a parameter, drag a hurricane track, click a point on an EP curve, and see
footprints, locations and policy terms respond immediately. It speaks Oasis **OED** (exposure) natively and will
emit **ORD**-style results, so portfolios move between the two without conversion.

## Architecture

```
            ┌───────────────────── web (React + TS, Vite) ─────────────────────┐
            │  typed client generated from schemas/openapi.json                 │
            │  [phase 3] MapLibre + deck.gl · ECharts · DuckDB-WASM over Arrow  │
            └──────────────▲──────────────────────────▲────────────────────────┘
                 JSON (REST)│               Arrow IPC / Parquet (columnar)
            ┌──────────────┴──────────────────────────┴────────────────────────┐
            │  API (FastAPI) — OpenAPI is the contract; WS/SSE for live (ph. 4) │
            ├───────────────────────────────────────────────────────────────────┤
            │  catmod engine (Python · NumPy · [ph. 2] Numba)                   │
            │   exposure ─ hazard ─ vulnerability ─ financial ─ metrics          │
            ├───────────────────────────────────────────────────────────────────┤
            │  storage: Parquet (zstd) + DuckDB, H3-indexed                     │
            └───────────────────────────────────────────────────────────────────┘
```

Design principles: **API-first** (UI is one client among SDK/CLI), **declarative config** (validated YAML with
published JSON Schema), **columnar end to end** (Arrow/Parquet, no row-wise JSON for bulk data),
**reproducible** (a config + seed is a portfolio identity), **open standards** (OED/ORD, H3).

## Quick start

```bash
make setup        # uv + npm
make api          # http://127.0.0.1:8000/docs
make web          # http://localhost:5173
# or the full stack in Docker:
make up           # http://localhost:8080
```

CLI:

```bash
cd backend
uv run catmod presets
uv run catmod generate --preset us-cat-demo --n 1000000 --out ../data/demo   # OED CSV + Parquet
uv run catmod generate --config my-portfolio.yaml --out ../data/mine
```

API (excerpt — full contract in [`schemas/openapi.json`](schemas/openapi.json)):

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/portfolios` | Generate from `preset` (+ `n_locations`/`seed` overrides) or an inline `config` |
| `GET` | `/api/v1/portfolios/{id}/aggregate?by=hub\|peril\|occupancy\|construction\|country\|h3&h3_res=` | Roll-ups (TIV, count, centroid) |
| `GET` | `/api/v1/portfolios/{id}/locations.arrow` | Arrow IPC stream for GPU map layers |
| `GET` | `/api/v1/portfolios/{id}/files/{location\|account}.parquet` | Raw Parquet for DuckDB-WASM |
| `GET` | `/api/v1/codes` | OED code lists (peril, occupancy, construction, deductible type) |

## Synthetic exposure model

Portfolios are generated from a validated YAML spec ([schema](schemas/portfolio-config.schema.json),
[example](backend/src/catmod/presets/us-cat-demo.yaml)):

- **Space** — mixture over hubs; radial distance follows Clark's urban density law `D(r) ∝ e^{-r/s}`, whose
  2-D radial pdf `∝ r·e^{-r/s}` is exactly `Gamma(2, s)`. Points are placed with great-circle destination math.
- **Building stock** — occupancy mix; construction conditional on *(hub, occupancy)* to encode regional practice
  (Florida masonry vs. California wood frame); storeys `min + Geometric(p) − 1`; year built truncated normal.
- **Values** — `value/storey × storeys × hub index × (1 + u·e^{-r/s}) × ε`, with mean-one lognormal `ε`.
  Contents and BI as noisy ratios of building value.
- **Accounts** — personal lines are single-location; commercial schedule sizes are `1 + Lomax(α)`, giving the
  heavy-tailed account concentration seen in real books. One policy/layer per account with participation,
  limit and attachment drawn from configured ranges.
- **Performance** — fully vectorised: 50k locations ≈ 0.2 s, 1M ≈ 3.4 s (H3 indexing dominates).

## Repository layout

```
backend/   Python package `catmod`: engine, CLI, API (uv, ruff, mypy --strict, pytest)
web/       React + TypeScript client (Vite)
schemas/   Generated contracts: portfolio-config JSON Schema, OpenAPI (CI fails on drift)
docker/    Images + nginx reverse proxy
docs/      Roadmap and architecture decision records
```

## License

[Apache-2.0](LICENSE).
