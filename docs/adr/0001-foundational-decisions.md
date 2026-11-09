# ADR 0001 — Foundational decisions

Status: accepted (2026-11-09)

## Decisions

1. **v1 perils: earthquake (QEQ) and tropical cyclone wind (WTC).** Both admit clean parametric hazard models and
   have free live feeds (USGS ShakeMap, NOAA NHC). Flood and wildfire need large raster inputs and come later.
2. **Compute locus: engine on the server, drill-down analytics in the browser.** The engine owns heavy event ×
   location kernels; the browser gets Arrow/Parquet and runs interactive slicing in DuckDB-WASM. A fully
   in-browser WebGPU engine would cap portfolio size.
3. **License: Apache-2.0**, for its explicit patent grant.
4. **Sequencing: phases 1–3 on synthetic data before live feeds**, so the nowcast sits on a validated engine.

## Consequences of the phase 1 build

- **OED as the exposure lingua franca.** Location/account CSVs are strict OED subsets; analytic extras live only in
  Parquet under a `catmod_` prefix, so CSV output stays loadable by Oasis.
- **Contracts are generated, committed and diff-checked.** Pydantic models → `schemas/*.json`; OpenAPI →
  TypeScript types via `openapi-typescript`. CI fails if either drifts.
- **Columnar transport.** Bulk location data is served as Arrow IPC / Parquet, never JSON.
- **TypeScript pinned to 5.x**: TS 7's native compiler drops the JS compiler API that `openapi-typescript` uses.
- **File-backed store.** One directory per portfolio (Parquet + `manifest.json`), rescanned at startup. Adequate
  until multi-user/auth in phase 6; the `PortfolioStore` interface is the seam for a DB-backed implementation.
