import { useEffect, useMemo, useState } from "react";
import { api, unwrap, type AnalysisConfig, type RunMeta } from "../api/client";
import { axisStyle, baseOption, Chart, chrome } from "../components/Chart";
import { Empty, Field, Panel, Progress, Status } from "../components/ui";
import { SERIES2 } from "../components/widgets";
import { fmtMoney, fmtRp, fmtTime } from "../lib/format";
import { useAsync, useRuns } from "../lib/hooks";
import { useMode, useStore } from "../state/store";

const _presets = () => unwrap(api.GET("/api/v1/analysis/presets"));
type Cfg = Awaited<ReturnType<typeof _presets>>[string];

function Launcher({ portfolioId }: { portfolioId: string }) {
  const presets = useAsync(_presets, []);
  const caps = useAsync(() => unwrap(api.GET("/api/v1/capabilities")), []);
  const [preset, setPreset] = useState("quick");
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [json, setJson] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const { toast, setRun, setView } = useStore.getState();

  useEffect(() => {
    const p = presets.data?.[preset];
    if (p) setCfg(structuredClone(p));
  }, [presets.data, preset]);
  useEffect(() => {
    if (cfg) setJson(JSON.stringify(cfg, null, 2));
  }, [cfg]);

  if (!cfg) return <p className="muted">Loading presets…</p>;
  const set = (patch: Partial<Cfg>) => setCfg({ ...cfg, ...patch });
  const dc = cfg.damage_correlation;
  const ri = { ...cfg.reinsurance, layers: cfg.reinsurance.layers ?? [] };
  const perils = cfg.perils ?? [];

  const launch = async () => {
    let body = cfg as unknown as AnalysisConfig;
    if (advanced) {
      try {
        body = JSON.parse(json) as AnalysisConfig;
      } catch (e) {
        toast({ tone: "critical", title: "Invalid JSON", body: String((e as Error).message) });
        return;
      }
    }
    setBusy(true);
    try {
      const run = await unwrap(api.POST("/api/v1/runs", { body: { portfolio_id: portfolioId, preset: null, config: body } }));
      toast({ tone: "info", title: "Analysis queued", body: run.name });
      useStore.getState().bump("runs");
      setRun(run.id);
      setView("risk");
    } catch (e) {
      toast({ tone: "critical", title: "Rejected by the engine", body: String((e as Error).message) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="launcher">
      <div className="controls">
        <Field label="Preset">
          <select value={preset} onChange={(e) => setPreset(e.target.value)}>
            {Object.entries(presets.data ?? {}).map(([k, v]) => <option key={k} value={k}>{k} — {v.name}</option>)}
          </select>
        </Field>
        <Field label="Name"><input value={cfg.name} onChange={(e) => set({ name: e.target.value })} /></Field>
        <Field label="Perils">
          <div className="row-gap">
            {(caps.data?.hazards ?? ["QEQ", "WTC"]).map((p) => (
              <label key={p} className="check"><input type="checkbox" checked={perils.includes(p)}
                onChange={(e) => set({ perils: e.target.checked ? [...perils, p] : perils.filter((x) => x !== p) })} />{p}</label>
            ))}
          </div>
        </Field>
        <Field label="Years" hint="Poisson-simulated"><input type="number" min={100} step={1000} value={cfg.n_years} onChange={(e) => set({ n_years: +e.target.value })} /></Field>
        <Field label="Samples / event" hint="secondary uncertainty"><input type="number" min={1} max={512} value={cfg.n_samples} onChange={(e) => set({ n_samples: +e.target.value })} /></Field>
        <Field label="Seed"><input type="number" min={0} value={cfg.seed} onChange={(e) => set({ seed: +e.target.value })} /></Field>
      </div>
      <h4>Correlation (pluggable)</h4>
      <div className="controls">
        <Field label="Damage copula">
          <select value={dc.model} onChange={(e) => set({ damage_correlation: { ...dc, model: e.target.value } })}>
            {(caps.data?.copulas ?? ["independent", "one_factor", "spatial"]).map((c) => <option key={c}>{c}</option>)}
          </select>
        </Field>
        <Field label={`ρ = ${dc.rho.toFixed(2)}`}><input type="range" min={0} max={0.95} step={0.05} value={dc.rho} onChange={(e) => set({ damage_correlation: { ...dc, rho: +e.target.value } })} /></Field>
        {dc.model === "spatial" && <Field label={`Range ${dc.range_km} km`}><input type="range" min={1} max={100} value={dc.range_km} onChange={(e) => set({ damage_correlation: { ...dc, range_km: +e.target.value } })} /></Field>}
        <Field label="EQ ground-motion field">
          <select value={cfg.eq.field.model} onChange={(e) => set({ eq: { ...cfg.eq, field: { ...cfg.eq.field, model: e.target.value } } })}>
            {(caps.data?.spatial_fields ?? ["fft"]).map((c) => <option key={c}>{c}</option>)}
          </select>
        </Field>
        <Field label={`EQ field range ${cfg.eq.field.range_km} km`}><input type="range" min={1} max={80} value={cfg.eq.field.range_km} onChange={(e) => set({ eq: { ...cfg.eq, field: { ...cfg.eq.field, range_km: +e.target.value } } })} /></Field>
        <Field label="TC wind field">
          <select value={cfg.tc.field.model} onChange={(e) => set({ tc: { ...cfg.tc, field: { ...cfg.tc.field, model: e.target.value } } })}>
            {(caps.data?.spatial_fields ?? ["fft"]).map((c) => <option key={c}>{c}</option>)}
          </select>
        </Field>
      </div>
      <h4>Reinsurance</h4>
      <div className="controls">
        <Field label={`Quota share ${(ri.quota_share * 100).toFixed(0)}%`}><input type="range" min={0} max={0.9} step={0.05} value={ri.quota_share} onChange={(e) => set({ reinsurance: { ...ri, quota_share: +e.target.value } })} /></Field>
        {ri.layers.map((l, i) => (
          <div key={i} className="row-gap layer-row">
            <Field label="Retention"><input type="number" min={0} step={1e6} value={l.retention} onChange={(e) => set({ reinsurance: { ...ri, layers: ri.layers.map((x, j) => j === i ? { ...x, retention: +e.target.value } : x) } })} /></Field>
            <Field label="Limit"><input type="number" min={1} step={1e6} value={l.limit} onChange={(e) => set({ reinsurance: { ...ri, layers: ri.layers.map((x, j) => j === i ? { ...x, limit: +e.target.value } : x) } })} /></Field>
            <Field label="Reinst."><input type="number" min={0} max={20} value={l.reinstatements} onChange={(e) => set({ reinsurance: { ...ri, layers: ri.layers.map((x, j) => j === i ? { ...x, reinstatements: +e.target.value } : x) } })} /></Field>
            <button className="ghost" aria-label="Remove layer" onClick={() => set({ reinsurance: { ...ri, layers: ri.layers.filter((_, j) => j !== i) } })}>✕</button>
          </div>
        ))}
        <button onClick={() => set({ reinsurance: { ...ri, layers: [...ri.layers, { name: `Layer ${ri.layers.length + 1}`, retention: 250e6, limit: 500e6, reinstatements: 1 }] } })}>+ XoL layer</button>
      </div>
      <details open={advanced} onToggle={(e) => setAdvanced((e.target as HTMLDetailsElement).open)}>
        <summary>Full configuration (JSON, validated server-side against the published schema)</summary>
        <textarea className="code" spellCheck={false} value={json} onChange={(e) => setJson(e.target.value)} rows={18} />
      </details>
      <div className="row-gap">
        <button className="primary" disabled={busy || !perils.length} onClick={launch}>{busy ? "Submitting…" : "Run analysis"}</button>
        <span className="muted small">Runs execute in a background process pool; progress streams over WebSocket.</span>
      </div>
    </div>
  );
}

function Compare({ runs }: { runs: RunMeta[] }) {
  const mode = useMode();
  const eps = useAsync(() => Promise.all(runs.map((r) => unwrap(api.GET("/api/v1/runs/{rid}/ep", { params: { path: { rid: r.id }, query: { perspective: "gross" } } })))), [runs.map((r) => r.id).join()]);
  const option = useMemo(() => {
    const c = chrome(mode);
    return {
      ...baseOption(mode),
      grid: { left: 64, right: 96, top: 28, bottom: 40 },
      legend: { top: 0, textStyle: { color: c.secondary }, icon: "path://M0,4 L14,4" },
      tooltip: { ...(baseOption(mode).tooltip as object), trigger: "axis", valueFormatter: (v: number) => fmtMoney(v) },
      xAxis: { type: "log", min: 1.5, name: "Return period (years)", nameLocation: "middle", nameGap: 26, ...axisStyle(mode), axisLabel: { ...axisStyle(mode).axisLabel, formatter: (v: number) => fmtRp(v) } },
      yAxis: { type: "value", ...axisStyle(mode), axisLabel: { ...axisStyle(mode).axisLabel, formatter: (v: number) => fmtMoney(v, "") } },
      series: (eps.data ?? []).map((ep, i) => ({
        name: runs[i]!.name, type: "line", showSymbol: false, lineStyle: { width: 2, color: SERIES2[mode][i] }, itemStyle: { color: SERIES2[mode][i] },
        endLabel: { show: true, formatter: runs[i]!.name.slice(0, 16), color: c.secondary, fontSize: 11 },
        data: ep.curve.filter((p) => p.rp >= 1.5).map((p) => [p.rp, p.oep]),
      })),
    };
  }, [eps.data, mode, runs]);
  const [a, b] = eps.data ?? [];
  return (
    <div className="grid">
      <div className="span-7"><Chart option={option} ariaLabel="OEP curves of the two compared runs" loading={eps.loading} /></div>
      <div className="span-5">
        {a && b && (
          <table className="table compact">
            <thead><tr><th>Gross metric</th><th className="num">{runs[0]!.name}</th><th className="num">{runs[1]!.name}</th><th className="num">Δ</th></tr></thead>
            <tbody>
              {([["AAL", a.metrics.aal, b.metrics.aal], ...["100", "250", "500"].map((r) => [`OEP 1-in-${r}`, a.metrics.oep[r] ?? 0, b.metrics.oep[r] ?? 0]),
                 ["TVaR 99%", a.metrics.tvar["0.99"] ?? 0, b.metrics.tvar["0.99"] ?? 0]] as [string, number, number][]).map(([k, x, y]) => (
                <tr key={k}><td>{k}</td><td className="num">{fmtMoney(x)}</td><td className="num">{fmtMoney(y)}</td>
                  <td className="num">{x ? `${(((y - x) / x) * 100).toFixed(1)}%` : "–"}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export function AnalysesView() {
  const pid = useStore((s) => s.portfolioId);
  const progress = useStore((s) => s.progress);
  const { setRun, setView, toast } = useStore.getState();
  const runs = useRuns(pid);
  const [cmp, setCmp] = useState<string[]>([]);
  if (!pid) return <div className="view"><Empty title="No portfolio selected">Generate or select a portfolio first.</Empty></div>;
  const del = async (id: string) => {
    await api.DELETE("/api/v1/runs/{rid}", { params: { path: { rid: id } } });
    useStore.getState().bump("runs");
    toast({ tone: "info", title: "Run deleted" });
  };
  const compared = (runs.data ?? []).filter((r) => cmp.includes(r.id));
  return (
    <div className="view">
      <div className="view-head"><div><h1>Analyses</h1><p className="muted">Configure the engine declaratively; every option maps 1:1 to <code>schemas/analysis-config.schema.json</code>.</p></div></div>
      <Panel title="New analysis"><Launcher portfolioId={pid} /></Panel>
      <Panel title="Runs" subtitle="Tick two finished runs to compare them.">
        {!runs.data?.length ? <p className="muted">No runs yet.</p> : (
          <table className="table compact">
            <thead><tr><th /><th>Name</th><th>Status</th><th>Copula</th><th className="num">AAL gross</th><th className="num">OEP 250</th><th className="num">TVaR 99</th><th>Created</th><th /></tr></thead>
            <tbody>
              {runs.data.map((r) => {
                const m = r.summary?.metrics.gross;
                const p = progress[r.id];
                return (
                  <tr key={r.id}>
                    <td><input type="checkbox" aria-label={`Compare ${r.name}`} disabled={r.status !== "done"} checked={cmp.includes(r.id)}
                      onChange={(e) => setCmp(e.target.checked ? [...cmp, r.id].slice(-2) : cmp.filter((x) => x !== r.id))} /></td>
                    <td><button className="link" onClick={() => { setRun(r.id); setView("risk"); }}>{r.name}</button></td>
                    <td>{r.status === "running" || r.status === "queued" ? <Progress value={p?.progress ?? r.progress} label={p?.message ?? r.status} />
                      : <Status tone={r.status === "done" ? "good" : "critical"}>{r.status}</Status>}</td>
                    <td className="small">{r.config.damage_correlation.model} ρ{r.config.damage_correlation.rho}</td>
                    <td className="num">{fmtMoney(m?.aal)}</td>
                    <td className="num">{fmtMoney(m?.oep["250"])}</td>
                    <td className="num">{fmtMoney(m?.tvar["0.99"])}</td>
                    <td className="small muted">{fmtTime(r.created_at)}</td>
                    <td><button className="ghost" aria-label={`Delete ${r.name}`} onClick={() => del(r.id)}>✕</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>
      {compared.length === 2 && <Panel title="Comparison (gross)"><Compare runs={compared} /></Panel>}
    </div>
  );
}
