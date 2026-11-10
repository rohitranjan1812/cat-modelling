# ADR 0002 — Engine, correlation plug-ins and explorer architecture

Status: accepted (2026-11-10)

## Decisions

1. **Correlation is a pluggable interface, not a fixed choice.** Hazard residual fields
   (`SpatialField`: independent / cholesky / fft) and damage copulas (`DamageCopula`: independent /
   one_factor / spatial) are registries extensible via entry points. The default is FFT ground
   motion + one-factor damage (ρ = 0.15); `catmod benchmark` quantifies the choice per portfolio.
2. **Enumerated, deterministic catalogues.** Rates are exact and catalogues are reproducible without
   storing them; any event can be regenerated from config + id.
3. **Per-event seeding** `(run seed, event id)`. Enables bit-for-bit drill-down recompute and
   worker-count-independent results, so the ELT need not store location-level losses.
4. **`spawn` process pool** for the event loop. `fork` from the multi-threaded API server can
   deadlock; workers rebuild the engine from the pickled exposure + config.
5. **FFT torus padded by 1.5 × range** (not 2N) — bounded ≈1% wrap error, up to 4× faster.
6. **Tabulated Beta inverse CDF** for secondary uncertainty — exact at nodes, ~100× faster.
7. **Segment loss table** (hub × occupancy × construction per event-sample) so EP curves respond to
   cross-filters server-side without re-running the model.
8. **Client crossfilter on typed arrays instead of DuckDB-WASM.** The location table is ≤ a few MB of
   Arrow; dictionary-encoded typed arrays + mask evaluation group-by 1M rows in < 10 ms with no 30 MB
   WASM payload. DuckDB-WASM remains an option for ad-hoc SQL.
9. **H3 hexes drawn with `h3-js` + deck.gl `PolygonLayer`**, dropping `@deck.gl/geo-layers`, which
   pulled a vulnerable `fflate` through loaders.gl 3D-tiles (`npm audit` now clean).
10. **Dataviz palette validated, not eyeballed.** Perils own categorical slots 1–2 (blue/orange,
    CVD ΔE ≥ 24.7 both modes); magnitudes use one-hue ramps (blue: AAL/TIV; orange: intensity);
    OEP/AEP use blue/aqua (validated; light-mode aqua carries direct labels + a table view).
11. **Single URL-serialised filter state** scopes every widget, map layer and server query.

## Consequences

* Net-of-reinsurance metrics cannot be filtered by segment (non-linear); the API returns 422 and the
  UI falls back to gross with an explanation.
* MapLibre v6 ships its worker as a separate ES module; it is bundled with `?worker&url` and
  registered via `setWorkerUrl` (works in Vite dev and production builds).
