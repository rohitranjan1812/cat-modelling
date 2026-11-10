# Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1. Foundations | Monorepo, OED schema, synthetic portfolio generator, API contracts, CI, Docker | done |
| 2. Engine v1 | EQ (GR catalogue, GMM + Vs30, correlated residuals) and TC (landfall catalogue, Holland + decay); pluggable spatial fields and damage copulas; Beta secondary uncertainty; location/account terms, QS + XoL; ELT/YLT, OEP/AEP, VaR/TVaR with SEs; closed-form validation | done |
| 3. Explorer UI | MapLibre + deck.gl H3 maps, typed-array crossfilter, URL-serialised filter state, EP → events → footprint → locations → terms drill path, treemap/heatmap/breakdowns, dark mode | done |
| 4. Live | USGS + NHC ingestion, ensemble nowcasts, alert rules with dedupe, WebSocket push, simulated-event injection | done |
| 5. Scenario lab & configurability | Interactive EQ/TC scenarios (click-to-place, draw tracks), declarative dashboards, analysis config editor, plugin registries, run comparison | done |
| 6. Hardening | API-key auth, Python SDK, CLI (`run`, `benchmark`, `scenario`), methodology docs, ADRs | done |

## Next

* **Science**: storm surge (SLOSH-lite bathtub on DEM), spectral-acceleration GMM with
  NGA-West2 coefficients, building-level secondary modifiers, land mask for synthetic exposure.
* **Scale**: Numba/Rust kernels for the wind field and financial aggregation; multi-node runs via
  Ray; tile-served hexes for > 5M locations.
* **Platform**: multi-tenant auth (OIDC), run lineage and config diffing, ORD-format exports,
  importing real OED portfolios through the UI with validation reports.
* **Live**: NHC forecast cones (GIS products) instead of persistence tracks, GDACS/JMA feeds,
  ShakeMap grids replacing GMM medians when available.
