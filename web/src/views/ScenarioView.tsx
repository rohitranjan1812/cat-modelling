import { useMemo, useState } from "react";
import { ScatterplotLayer } from "@deck.gl/layers";
import { api, unwrap } from "../api/client";
import { EventPanel } from "../components/EventPanel";
import { MapView } from "../components/MapView";
import { Empty, Field, Panel, Segmented } from "../components/ui";
import { footprintLayers, RampLegend } from "../components/widgets";
import { useAsync, useCodes, useDebounced } from "../lib/hooks";
import { ORANGE_RAMP, rgb } from "../lib/palette";
import { useMode, useStore } from "../state/store";

type Peril = "QEQ" | "WTC";
type TcMode = "landfall" | "track";

function category(v: number): string {
  if (v >= 70) return "Cat 5";
  if (v >= 58) return "Cat 4";
  if (v >= 50) return "Cat 3";
  if (v >= 43) return "Cat 2";
  if (v >= 33) return "Cat 1";
  return "TS";
}

export function ScenarioView() {
  const mode = useMode();
  const pid = useStore((s) => s.portfolioId);
  const toast = useStore((s) => s.toast);
  const codes = useCodes();
  const [peril, setPeril] = useState<Peril>("QEQ");
  const [eq, setEq] = useState({ lat: 34.02, lon: -118.32, magnitude: 7.0, depth_km: 10, strike_deg: 300 });
  const [tcMode, setTcMode] = useState<TcMode>("landfall");
  const [tc, setTc] = useState({ lat: 25.75, lon: -80.25, vmax_ms: 60, heading_deg: 300, translation_ms: 6, rmax_km: 0 });
  const [track, setTrack] = useState<{ lat: number; lon: number; vmax_ms: number }[]>([]);
  const [samples, setSamples] = useState(64);
  const [corr, setCorr] = useState({ model: "one_factor", rho: 0.15 });

  const event = useMemo(() => {
    if (peril === "QEQ") return eq;
    if (tcMode === "track") return track.length >= 2 ? { track } : null;
    const { rmax_km, ...rest } = tc;
    return rmax_km > 0 ? tc : rest;
  }, [peril, eq, tc, tcMode, track]);
  const req = useDebounced(JSON.stringify({ pid, peril, event, samples, corr }), 350);
  const res = useAsync(() => {
    const r = JSON.parse(req) as { pid: string | null; peril: Peril; event: Record<string, unknown> | null; samples: number; corr: { model: string; rho: number } };
    if (!r.pid || !r.event) return null;
    return unwrap(api.POST("/api/v1/scenarios", { body: {
      portfolio_id: r.pid, peril: r.peril, event: r.event, n_samples: r.samples, top: 50, seed: 7,
      damage_correlation: { model: r.corr.model, rho: r.corr.rho, rho_by_construction: {}, range_km: 10, field: "fft" } } }));
  }, [req]);

  const onMapClick = (lon: number, lat: number) => {
    if (peril === "QEQ") setEq((e) => ({ ...e, lat, lon }));
    else if (tcMode === "landfall") setTc((t) => ({ ...t, lat, lon }));
    else setTrack((tr) => [...tr, { lat, lon, vmax_ms: tc.vmax_ms }]);
  };

  const layers = useMemo(() => {
    const out = res.data ? footprintLayers(res.data.footprint, res.data.geometry, mode, res.data.peril) : [];
    const pins = peril === "QEQ" ? [eq] : tcMode === "landfall" ? [tc] : track;
    out.push(new ScatterplotLayer({
      id: "pins", data: pins, getPosition: (d: { lon: number; lat: number }) => [d.lon, d.lat],
      getFillColor: [...rgb(mode === "dark" ? "#f0efec" : "#0b0b0b"), 255], getRadius: 6, radiusUnits: "pixels",
      stroked: true, getLineColor: [255, 255, 255, 255], lineWidthMinPixels: 2,
    }) as never);
    return out;
  }, [res.data, mode, peril, eq, tc, tcMode, track]);

  const inject = async () => {
    const body = peril === "QEQ"
      ? { peril, name: `Simulated M${eq.magnitude.toFixed(1)}`, lat: eq.lat, lon: eq.lon, magnitude: eq.magnitude, depth_km: eq.depth_km }
      : { peril, name: `Simulated ${category(tc.vmax_ms)} hurricane`, lat: tc.lat, lon: tc.lon, vmax_ms: tc.vmax_ms, heading_deg: tc.heading_deg, speed_ms: tc.translation_ms };
    try {
      await unwrap(api.POST("/api/v1/live/inject", { body: body as never }));
      toast({ tone: "good", title: "Sent to Live", body: "Nowcast and alert rules evaluated." });
    } catch (e) {
      toast({ tone: "critical", title: "Inject failed", body: String((e as Error).message) });
    }
  };

  if (!pid) return <div className="view"><Empty title="No portfolio selected">Pick a portfolio to stress-test.</Empty></div>;
  return (
    <div className="view with-drawer">
      <div className="view-head">
        <div>
          <h1>Scenario lab</h1>
          <p className="muted">Click the map to place the {peril === "QEQ" ? "epicentre" : tcMode === "landfall" ? "landfall point" : "next track point"}. Results recompute live with full secondary uncertainty.</p>
        </div>
        <div className="row-gap">
          {res.data && <span className="muted small">{res.data.elapsed_ms} ms</span>}
          {peril === "QEQ" || tcMode === "landfall" ? <button onClick={inject}>Send to Live as simulated event</button> : null}
        </div>
      </div>
      <Panel>
        <div className="controls">
          <Segmented label="Peril" value={peril} onChange={setPeril} options={[{ value: "QEQ", label: "Earthquake" }, { value: "WTC", label: "Hurricane" }]} />
          {peril === "QEQ" ? (
            <>
              <Field label={`Magnitude M${eq.magnitude.toFixed(1)}`}><input type="range" min={5} max={9.2} step={0.1} value={eq.magnitude} onChange={(e) => setEq({ ...eq, magnitude: +e.target.value })} /></Field>
              <Field label={`Depth ${eq.depth_km} km`}><input type="range" min={2} max={60} step={1} value={eq.depth_km} onChange={(e) => setEq({ ...eq, depth_km: +e.target.value })} /></Field>
              <Field label={`Strike ${eq.strike_deg}°`}><input type="range" min={0} max={179} step={1} value={eq.strike_deg} onChange={(e) => setEq({ ...eq, strike_deg: +e.target.value })} /></Field>
            </>
          ) : (
            <>
              <Segmented label="Track input" value={tcMode} onChange={setTcMode} options={[{ value: "landfall", label: "Landfall" }, { value: "track", label: "Draw track" }]} />
              <Field label={`Vmax ${tc.vmax_ms} m/s · ${category(tc.vmax_ms)}`}><input type="range" min={25} max={85} step={1} value={tc.vmax_ms} onChange={(e) => setTc({ ...tc, vmax_ms: +e.target.value })} /></Field>
              {tcMode === "landfall" ? (
                <>
                  <Field label={`Heading ${tc.heading_deg}°`}><input type="range" min={0} max={359} step={5} value={tc.heading_deg} onChange={(e) => setTc({ ...tc, heading_deg: +e.target.value })} /></Field>
                  <Field label={`Rmax ${tc.rmax_km ? `${tc.rmax_km} km` : "auto (Willoughby)"}`}><input type="range" min={0} max={100} step={5} value={tc.rmax_km} onChange={(e) => setTc({ ...tc, rmax_km: +e.target.value })} /></Field>
                  <Field label={`Speed ${tc.translation_ms} m/s`}><input type="range" min={2} max={15} step={0.5} value={tc.translation_ms} onChange={(e) => setTc({ ...tc, translation_ms: +e.target.value })} /></Field>
                </>
              ) : (
                <div className="row-gap"><span className="muted small">{track.length} points</span><button className="ghost" onClick={() => setTrack([])}>Clear track</button></div>
              )}
            </>
          )}
          <Field label="Samples"><select value={samples} onChange={(e) => setSamples(+e.target.value)}>{[16, 64, 256, 1000].map((n) => <option key={n} value={n}>{n}</option>)}</select></Field>
          <Field label="Damage correlation">
            <select value={corr.model} onChange={(e) => setCorr({ ...corr, model: e.target.value })}>
              <option value="independent">independent</option><option value="one_factor">one-factor</option><option value="spatial">spatial</option>
            </select>
          </Field>
          <Field label={`ρ ${corr.rho.toFixed(2)}`}><input type="range" min={0} max={0.9} step={0.05} value={corr.rho} onChange={(e) => setCorr({ ...corr, rho: +e.target.value })} /></Field>
        </div>
      </Panel>
      <Panel className={res.loading ? "refetching" : ""}>
        <MapView mode={mode} layers={layers} height={560} onMapClick={onMapClick} cursor="crosshair"
          bounds={peril === "QEQ" ? [-123.5, 32, -116, 38.6] : [-98, 24, -76, 35.5]}
          tooltip={(info) => (info.object as { label?: string } | undefined)?.label ?? null}>
          {res.data && <RampLegend label={`Intensity (${res.data.unit})`} stops={ORANGE_RAMP} lo="low" hi="high" />}
        </MapView>
        {res.error && <p className="error">{res.error}</p>}
        {res.data && res.data.n_affected === 0 && <p className="note">No covered locations inside this footprint — move the event closer to the exposure.</p>}
      </Panel>
      {res.data && res.data.n_affected > 0 && <div className="drawer"><EventPanel d={res.data} mode={mode} codes={codes} /></div>}
    </div>
  );
}
