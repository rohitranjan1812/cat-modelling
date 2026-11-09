# Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1. Foundations | Monorepo, OED schema, synthetic portfolio generator, API skeleton + contracts, web shell, CI, Docker | **done** |
| 2. Engine v1 | EQ (GMM + Vs30 + spatially correlated residuals) and TC (Holland wind field over synthetic tracks) hazard on H3; vulnerability with correlated secondary uncertainty; financial module (location/policy terms, QS/XoL); ELT/YLT, AAL, OEP/AEP, VaR/TVaR with standard errors; closed-form validation tests | next |
| 3. Explorer UI | MapLibre + deck.gl, DuckDB-WASM, single cross-filter state serialised to URL, EP-curve → events → footprint → locations → terms drill path | |
| 4. Live | USGS / ShakeMap, NOAA NHC, GDACS, NWS ingestors; ensemble nowcast of live events; alert rules over WebSocket | |
| 5. Scenario lab & configurability | Interactive what-ifs (drag tracks/epicentres), declarative dashboards, plugin API for perils/vulnerability | |
| 6. Hardening | Docs site, benchmarks, demo datasets, auth | |

## Open design problem: correlated ground-motion fields at scale

Dense Cholesky of the Jayaram–Baker correlation matrix is O(n³) per event. Plan: FFT circulant embedding on a
regular lat/lon grid resampled to H3, with truncation error measured against exact Cholesky on small cases.
Alternative under evaluation: Nyström low-rank on H3 centroids (risk: smooths short-range correlation that
drives tail losses for concentrated portfolios).
