import { useMemo, useState } from "react";
import { ScatterplotLayer } from "@deck.gl/layers";
import { api, unwrap, type AlertRule, type LiveEvent } from "../api/client";
import { EventPanel } from "../components/EventPanel";
import { MapView } from "../components/MapView";
import { Empty, Field, Panel, Status } from "../components/ui";
import { footprintLayers } from "../components/widgets";
import { fmtInt, fmtMoney, fmtTime } from "../lib/format";
import { useAsync, useCodes, usePortfolios } from "../lib/hooks";
import { geometryBbox } from "../lib/layers";
import { PERIL_LABEL, perilColor, rgb } from "../lib/palette";
import { useMode, useStore } from "../state/store";

const METRICS = ["p90_gross", "mean_gross", "p99_gross", "mean_gu", "p90_gu", "n_affected"] as const;

function RuleEditor() {
  const versions = useStore((s) => s.versions);
  const portfolios = usePortfolios();
  const toast = useStore((s) => s.toast);
  const rules = useAsync(() => unwrap(api.GET("/api/v1/alerts/rules")), [versions.alerts]);
  const [draft, setDraft] = useState<Omit<AlertRule, "id">>({ name: "Large gross loss", peril: null, portfolio_id: null, metric: "p90_gross", threshold: 100_000_000, enabled: true });
  const [, force] = useState(0);
  const save = async () => {
    try {
      await unwrap(api.POST("/api/v1/alerts/rules", { body: draft as AlertRule }));
      useStore.getState().bump("alerts");
      force((x) => x + 1);
    } catch (e) {
      toast({ tone: "critical", title: "Rule rejected", body: String((e as Error).message) });
    }
  };
  const del = async (id: string) => {
    await api.DELETE("/api/v1/alerts/rules/{rule_id}", { params: { path: { rule_id: id } } });
    useStore.getState().bump("alerts");
  };
  return (
    <>
      <table className="table compact">
        <thead><tr><th>Rule</th><th>Scope</th><th>Condition</th><th /></tr></thead>
        <tbody>
          {(rules.data ?? []).map((r) => (
            <tr key={r.id}>
              <td>{r.name}</td>
              <td className="small muted">{r.peril ?? "any peril"} · {portfolios.data?.find((p) => p.id === r.portfolio_id)?.name ?? "any portfolio"}</td>
              <td className="small">{r.metric} ≥ {r.metric === "n_affected" ? fmtInt(r.threshold) : fmtMoney(r.threshold)}</td>
              <td><button className="ghost" aria-label={`Delete ${r.name}`} onClick={() => del(r.id!)}>✕</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="inline-form">
        <Field label="Name"><input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Field>
        <Field label="Peril"><select value={draft.peril ?? ""} onChange={(e) => setDraft({ ...draft, peril: e.target.value || null })}><option value="">any</option><option value="QEQ">QEQ</option><option value="WTC">WTC</option></select></Field>
        <Field label="Metric"><select value={draft.metric} onChange={(e) => setDraft({ ...draft, metric: e.target.value as AlertRule["metric"] })}>{METRICS.map((m) => <option key={m}>{m}</option>)}</select></Field>
        <Field label="Threshold"><input type="number" min={0} value={draft.threshold} onChange={(e) => setDraft({ ...draft, threshold: +e.target.value })} /></Field>
        <button className="primary" onClick={save}>Add rule</button>
      </div>
    </>
  );
}

export function LiveView() {
  const mode = useMode();
  const codes = useCodes();
  const pid = useStore((s) => s.portfolioId);
  const versions = useStore((s) => s.versions);
  const toast = useStore((s) => s.toast);
  const status = useAsync(() => unwrap(api.GET("/api/v1/live/status")), [versions.live]);
  const events = useAsync(() => unwrap(api.GET("/api/v1/live/events")), [versions.live]);
  const alerts = useAsync(() => unwrap(api.GET("/api/v1/alerts")), [versions.alerts, versions.live]);
  const [sel, setSel] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const nowcastFor = (e: LiveEvent) => e.nowcasts?.find((n) => n.portfolio_id === pid) ?? e.nowcasts?.[0];
  const selected = events.data?.find((e) => e.id === sel) ?? null;
  const nc = selected ? nowcastFor(selected) : undefined;

  const layers = useMemo(() => {
    const evs = events.data ?? [];
    const out = [
      new ScatterplotLayer<LiveEvent>({
        id: "live-events", data: evs, pickable: true,
        getPosition: (e) => [e.lon, e.lat],
        getFillColor: (e) => [...rgb(perilColor(e.peril, mode)), e.id === sel ? 255 : 200],
        getRadius: (e) => (e.magnitude ? 4 + (e.magnitude - 4) * 4 : e.vmax_ms ? 6 + e.vmax_ms / 6 : 6),
        radiusUnits: "pixels", stroked: true, getLineColor: [255, 255, 255, 230], lineWidthMinPixels: 1.5,
        onClick: (info) => info.object && setSel(info.object.id),
        updateTriggers: { getFillColor: [sel, mode] },
      }),
    ];
    if (selected && nc) out.push(...(footprintLayers(nc.footprint, selected.geometry, mode, selected.peril) as never[]));
    return out;
  }, [events.data, sel, selected, nc, mode]);

  const refresh = async () => {
    setBusy(true);
    try {
      await unwrap(api.POST("/api/v1/live/refresh"));
      useStore.getState().bump("live");
    } catch (e) {
      toast({ tone: "critical", title: "Refresh failed", body: String((e as Error).message) });
    } finally {
      setBusy(false);
    }
  };

  const drill = selected && nc ? {
    event_id: 0, peril: selected.peril, name: selected.name, rate: 0, unit: selected.peril === "QEQ" ? "PGA (g)" : "Wind (m/s)",
    geometry: selected.geometry, summary: {}, n_affected: nc.n_affected, gu: nc.gu, gross: nc.gross, samples_gu: [], samples_gross: [],
    footprint: nc.footprint, top_locations: nc.top_locations, by_hub: nc.by_hub,
  } : null;

  return (
    <div className={`view ${drill ? "with-drawer" : ""}`}>
      <div className="view-head">
        <div>
          <h1>Live monitoring</h1>
          <p className="muted">
            USGS earthquakes (M4.5+, 7 days) and NOAA NHC active storms, nowcast against the {status.data ? "3 most recent" : ""} portfolios with ensemble secondary uncertainty.{" "}
            {status.data?.last_refresh ? `Last refresh ${fmtTime(status.data.last_refresh)}.` : "Not refreshed yet."}
            {status.data && !status.data.enabled && " Background polling is off (CATMOD_LIVE=1 enables it)."}
          </p>
          {status.data?.last_error && <p className="note">{status.data.last_error}</p>}
        </div>
        <button className="primary" disabled={busy} onClick={refresh}>{busy ? "Fetching feeds…" : "Refresh feeds now"}</button>
      </div>
      <div className="grid">
        <Panel className="span-8" title="Events" subtitle="Marker size ~ magnitude / wind; click to see the nowcast footprint and losses.">
          <MapView mode={mode} layers={layers} height={480} bounds={selected ? geometryBbox(selected.geometry as { coordinates: unknown }) : null}
            tooltip={(info) => { const e = info.object as LiveEvent | undefined; return e?.name ? `${e.name}\n${PERIL_LABEL[e.peril] ?? e.peril}` : (info.object as { label?: string } | undefined)?.label ?? null; }} />
        </Panel>
        <Panel className="span-4" title="Alerts" subtitle="Rules are evaluated on every nowcast and de-duplicated per event.">
          {!alerts.data?.length ? <p className="muted small">No alerts fired.</p> : (
            <ul className="alert-list">
              {alerts.data.slice(0, 12).map((a) => (
                <li key={a.id}>
                  <Status tone="critical">{a.rule_name}</Status>
                  <div className="small">{a.event_name}</div>
                  <div className="muted small">{a.metric} {a.metric === "n_affected" ? fmtInt(a.value) : fmtMoney(a.value)} · {fmtTime(a.created_at)}</div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel className="span-12" title="Feed">
          {events.data && events.data.length > 40 && <p className="muted small">Showing the 40 most relevant of {events.data.length} events (exposed first, then most recent).</p>}
          {!events.data?.length ? <Empty title="No live events">Refresh the feeds, or send a simulated event from the Scenario lab.</Empty> : (
            <table className="table compact clickable">
              <thead><tr><th>Event</th><th>Source</th><th className="num">Size</th><th>Time</th><th className="num">Locations hit</th><th className="num">Gross mean</th><th className="num">Gross P90</th><th>Status</th></tr></thead>
              <tbody>
                {[...events.data].sort((a, b) => Number(!!nowcastFor(b)) - Number(!!nowcastFor(a)) || b.time.localeCompare(a.time)).slice(0, 40).map((e) => {
                  const n = nowcastFor(e);
                  return (
                    <tr key={e.id} tabIndex={0} className={sel === e.id ? "selected" : ""} onClick={() => setSel(e.id)} onKeyDown={(k) => k.key === "Enter" && setSel(e.id)}>
                      <td className="truncate" title={e.name}><span className="dot" style={{ background: perilColor(e.peril, mode) }} />{e.name}</td>
                      <td className="small">{e.source}</td>
                      <td className="num">{e.magnitude ? `M${e.magnitude.toFixed(1)}` : e.vmax_ms ? `${e.vmax_ms} m/s` : "–"}</td>
                      <td className="small">{fmtTime(e.time)}</td>
                      <td className="num">{n ? fmtInt(n.n_affected) : "–"}</td>
                      <td className="num">{n ? fmtMoney(n.gross.mean) : "–"}</td>
                      <td className="num">{n ? fmtMoney(n.gross.p90) : "–"}</td>
                      <td>{n ? <Status tone={n.gross.p90 > 0 ? "warning" : "good"}>{n.gross.p90 > 0 ? "Exposed" : "No loss"}</Status> : <span className="muted small">no exposure nearby</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Panel>
        <Panel className="span-12" title="Alert rules"><RuleEditor /></Panel>
      </div>
      {drill && <div className="drawer"><EventPanel d={drill} mode={mode} codes={codes} onClose={() => setSel(null)}
        extra={<p className="muted small">Nowcast for {nc!.portfolio_name} · {nc!.elapsed_ms} ms{selected?.url && <> · <a href={selected.url} target="_blank" rel="noreferrer">source</a></>}</p>} /></div>}
    </div>
  );
}
