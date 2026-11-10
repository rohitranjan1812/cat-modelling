import { useMemo, useState } from "react";
import { api, unwrap, type Perspective } from "../api/client";
import { EventPanel } from "../components/EventPanel";
import { Empty, Panel, Progress, Segmented } from "../components/ui";
import {
  Breakdown, EpChart, EventsTable, footprintLayers, Heatmap, HexMap, KpiStrip, RampLegend, RpTable, Treemap,
} from "../components/widgets";
import { activeCount } from "../lib/columns";
import { fmtInt, fmtMoney, fmtRate, fmtRp } from "../lib/format";
import { useAsync, useCodes, useLocTable, usePortfolios, useRuns } from "../lib/hooks";
import { geometryBbox } from "../lib/layers";
import { BLUE_RAMP, ORANGE_RAMP, perilColor } from "../lib/palette";
import { useMode, useStore } from "../state/store";

export function useEp(runId: string | null, perspective: Perspective) {
  const f = useStore((s) => s.filters);
  const segmented = f.hubs.length + f.occupancies.length + f.constructions.length > 0;
  const effective: Perspective = perspective === "net" && segmented ? "gross" : perspective;
  const v = useStore((s) => s.versions.runs);
  const ep = useAsync(
    () => (runId ? unwrap(api.GET("/api/v1/runs/{rid}/ep", { params: { path: { rid: runId }, query: {
      perspective: effective, peril: f.perils, hub: f.hubs, occupancy: f.occupancies, construction: f.constructions } } })) : null),
    [runId, effective, f.perils.join(), f.hubs.join(), f.occupancies.join(), f.constructions.join(), v],
  );
  return { ep, effective, netFallback: effective !== perspective };
}

export function RiskView() {
  const mode = useMode();
  const { portfolioId, runId, eventId, perspective, filters } = useStore();
  const { setEvent, setView } = useStore.getState();
  const progress = useStore((s) => s.progress);
  const runs = useRuns(portfolioId);
  const portfolios = usePortfolios();
  const codes = useCodes();
  const run = runs.data?.find((r) => r.id === runId);
  const done = run?.status === "done";
  const t = useLocTable("run", done ? runId : null);
  const { ep, effective, netFallback } = useEp(done ? runId : null, perspective);
  const [rp, setRp] = useState<number | null>(null);
  const [res, setRes] = useState<"5" | "7">("5");
  const atRp = useAsync(() => (done && rp ? unwrap(api.GET("/api/v1/runs/{rid}/events-at-rp", {
    params: { path: { rid: runId! }, query: { rp, curve: "oep", perspective: effective === "net" ? "gross" : effective, window: 0.15, limit: 12 } } })) : null),
    [runId, rp, effective, done]);
  const drill = useAsync(() => (done && eventId != null ? unwrap(api.GET("/api/v1/runs/{rid}/events/{event_id}", {
    params: { path: { rid: runId!, event_id: eventId }, query: { top: 50 } } })) : null), [runId, eventId, done]);
  const tiv = useMemo(() => portfolios.data?.find((p) => p.id === portfolioId)?.summary.tiv_total, [portfolios.data, portfolioId]);
  const overlay = useMemo(() => (drill.data ? footprintLayers(drill.data.footprint, drill.data.geometry, mode, drill.data.peril) : []), [drill.data, mode]);
  const eventBounds = useMemo(() => (drill.data ? geometryBbox(drill.data.geometry as { coordinates: unknown }) : null), [drill.data]);

  if (!portfolioId) return <div className="view"><Empty title="No portfolio selected">Pick one in the top bar, or generate one under Exposure.</Empty></div>;
  if (!run) {
    return (
      <div className="view">
        <Empty title={runs.data?.length ? "Select an analysis run" : "No analyses yet for this portfolio"}>
          <button className="primary" onClick={() => setView("analyses")}>Launch an analysis</button>
        </Empty>
      </div>
    );
  }
  if (!done) {
    const p = progress[run.id] ?? { progress: run.progress, message: run.message };
    return (
      <div className="view">
        <Panel title={`${run.name} — ${run.status}`} subtitle={run.error?.split("\n")[0]}>
          <Progress value={p.progress} label={p.message} />
        </Panel>
      </div>
    );
  }

  const nFilters = activeCount(filters);
  return (
    <div className={`view ${drill.data ? "with-drawer" : ""}`}>
      <div className="view-head">
        <div>
          <h1>{run.name}</h1>
          <p className="muted">
            {(run.config.perils ?? []).join(" + ")} · {fmtInt(run.summary?.n_events)} events ({fmtInt(run.summary?.n_loss_events)} loss-causing) ·{" "}
            damage correlation <b>{run.config.damage_correlation.model}</b> ρ={run.config.damage_correlation.rho} · {run.summary?.elapsed_s}s
          </p>
        </div>
        <a className="button" href={`/api/v1/runs/${run.id}/files/ylt.parquet`}>Download YLT</a>
      </div>
      {netFallback && <p className="note">Net losses aren't separable by hub/occupancy/construction (reinsurance is non-linear) — showing gross for this slice.</p>}
      {filters.h3.length > 0 && <p className="note">Map-cell filters scope the map and breakdowns; EP metrics use peril/hub/occupancy/construction filters.</p>}
      <KpiStrip ep={ep.data} run={run} perspective={effective} tiv={nFilters ? undefined : tiv} />
      <div className="grid">
        <Panel className="span-7" title="Exceedance probability" subtitle="Click a point to list the events driving that return period.">
          <EpChart ep={ep.data} mode={mode} selectedRp={rp} onPick={setRp} loading={ep.loading} />
        </Panel>
        <Panel className="span-5" title={rp ? `Events near 1-in-${fmtRp(rp)} OEP` : "Return-period table"}
               actions={rp ? <button className="ghost" onClick={() => setRp(null)}>Back to table</button> : undefined}
               subtitle={rp && atRp.data ? `OEP level ${fmtMoney(atRp.data.level)} ± 15%; occurrences in the simulated years` : undefined}>
          {rp ? (
            <table className={`table compact clickable ${atRp.loading ? "refetching" : ""}`}>
              <thead><tr><th>Event</th><th className="num">Hits</th><th className="num">Mean gross</th><th className="num">Freq.</th></tr></thead>
              <tbody>
                {(atRp.data?.events ?? []).map((e, i) => (
                  <tr key={e.event_id} tabIndex={0} className={eventId === e.event_id ? "selected" : ""} onClick={() => setEvent(e.event_id)} onKeyDown={(k) => k.key === "Enter" && setEvent(e.event_id)}>
                    <td className="truncate" title={e.name}><span className="dot" style={{ background: perilColor(e.peril, mode) }} />{e.name}</td>
                    <td className="num">{atRp.data!.occurrences[i]}</td>
                    <td className="num">{fmtMoney(e.mean_gross)}</td>
                    <td className="num">{fmtRate(e.rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <RpTable ep={ep.data} />}
        </Panel>
        {t.data && (
          <>
            <Panel className="span-8" title={drill.data ? `Footprint: ${drill.data.name}` : "Location AAL"}
                   actions={<Segmented label="Hex resolution" value={res} onChange={setRes} options={[{ value: "5", label: "r5" }, { value: "7", label: "r7" }]} />}>
              <HexMap table={t.data} mode={mode} metric="aal" res={res} height={480} overlay={overlay} bounds={eventBounds}
                legend={<>
                  <RampLegend label={`AAL (${perspective === "gu" ? "GU" : "gross"}) per cell`} stops={BLUE_RAMP.slice(3)} lo="low" hi="high" />
                  {drill.data && <RampLegend label={`Event intensity (${drill.data.unit})`} stops={ORANGE_RAMP} lo="low" hi="high" />}
                </>} />
            </Panel>
            <Panel className="span-4" title="AAL by hub"><Breakdown table={t.data} dim="hub" metric="aal" mode={mode} codes={codes} /></Panel>
            <Panel className="span-4" title="AAL by peril"><Breakdown table={t.data} dim="peril" metric="aal" mode={mode} codes={codes} /></Panel>
            <Panel className="span-4" title="AAL by occupancy"><Breakdown table={t.data} dim="occupancy" metric="aal" mode={mode} codes={codes} /></Panel>
            <Panel className="span-4" title="AAL by construction"><Breakdown table={t.data} dim="construction" metric="aal" mode={mode} codes={codes} /></Panel>
            <Panel className="span-6" title="AAL: peril › hub › occupancy"><Treemap table={t.data} mode={mode} codes={codes} /></Panel>
            <Panel className="span-6" title="AAL: hub × construction"><Heatmap table={t.data} mode={mode} codes={codes} /></Panel>
          </>
        )}
        <Panel className="span-12" title="Largest events" subtitle="Ranked by mean gross loss; equivalent RP = 1 / cumulative frequency of events at least this large.">
          <EventsTable runId={run.id} />
        </Panel>
      </div>
      {drill.data && <div className="drawer"><EventPanel d={drill.data} mode={mode} codes={codes} onClose={() => setEvent(null)} /></div>}
      {drill.error && <p className="error">{drill.error}</p>}
    </div>
  );
}
