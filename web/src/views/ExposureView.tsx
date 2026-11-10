import { useMemo, useState } from "react";
import { api, unwrap } from "../api/client";
import { Panel, Field, Segmented, Empty } from "../components/ui";
import { Breakdown, HexMap, RampLegend, Treemap, Heatmap } from "../components/widgets";
import { axisStyle, baseOption, Chart } from "../components/Chart";
import { mask } from "../lib/columns";
import { fmtInt, fmtMoney } from "../lib/format";
import { useAsync, useCodes, useLocTable, usePortfolios } from "../lib/hooks";
import { BLUE_RAMP } from "../lib/palette";
import { useMode, useStore } from "../state/store";

function GenerateForm({ onDone }: { onDone: (id: string) => void }) {
  const presets = useAsync(() => unwrap(api.GET("/api/v1/presets")), []);
  const [preset, setPreset] = useState("us-cat-demo");
  const [n, setN] = useState(50_000);
  const [seed, setSeed] = useState(42);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useStore((s) => s.toast);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const m = await unwrap(api.POST("/api/v1/portfolios", { body: { preset, n_locations: n, seed, name: name || null } }));
      toast({ tone: "good", title: "Portfolio generated", body: `${fmtInt(m.summary.n_locations)} locations in ${m.generation_ms} ms` });
      onDone(m.id);
    } catch (err) {
      toast({ tone: "critical", title: "Generation failed", body: String((err as Error).message) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="inline-form" onSubmit={submit}>
      <Field label="Preset">
        <select value={preset} onChange={(e) => setPreset(e.target.value)}>
          {(presets.data ?? [preset]).map((p) => <option key={p}>{p}</option>)}
        </select>
      </Field>
      <Field label="Locations"><input type="number" min={100} max={5_000_000} step={1000} value={n} onChange={(e) => setN(+e.target.value)} /></Field>
      <Field label="Seed"><input type="number" min={0} value={seed} onChange={(e) => setSeed(+e.target.value)} /></Field>
      <Field label="Name"><input placeholder="(from preset)" value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <button className="primary" disabled={busy}>{busy ? "Generating…" : "Generate"}</button>
    </form>
  );
}

function YearBuilt({ years, m, mode }: { years: Int32Array; m: Uint8Array; mode: "light" | "dark" }) {
  const option = useMemo(() => {
    const bins = new Map<number, number>();
    for (let i = 0; i < years.length; i++) if (m[i]) bins.set(Math.floor(years[i]! / 10) * 10, (bins.get(Math.floor(years[i]! / 10) * 10) ?? 0) + 1);
    const keys = [...bins.keys()].sort();
    return {
      ...baseOption(mode),
      grid: { left: 44, right: 8, top: 8, bottom: 24 },
      tooltip: { ...(baseOption(mode).tooltip as object), trigger: "item", formatter: (p: { name: string; value: number }) => `<b>${fmtInt(p.value)}</b> locations<br/>${p.name}s` },
      xAxis: { type: "category", data: keys.map(String), ...axisStyle(mode) },
      yAxis: { type: "value", ...axisStyle(mode), axisLabel: { ...axisStyle(mode).axisLabel, formatter: (v: number) => fmtMoney(v, "") } },
      series: [{ type: "bar", data: keys.map((k) => bins.get(k)), barCategoryGap: "12%", itemStyle: { color: mode === "dark" ? "#3987e5" : "#2a78d6", borderRadius: [4, 4, 0, 0] } }],
    };
  }, [years, m, mode]);
  return <Chart option={option} height={180} ariaLabel="Locations by decade built" />;
}

export function ExposureView() {
  const mode = useMode();
  const pid = useStore((s) => s.portfolioId);
  const setPortfolio = useStore((s) => s.setPortfolio);
  const filters = useStore((s) => s.filters);
  const portfolios = usePortfolios();
  const codes = useCodes();
  const t = useLocTable("portfolio", pid);
  const [res, setRes] = useState<"5" | "7">("5");
  const [showForm, setShowForm] = useState(false);
  const meta = portfolios.data?.find((p) => p.id === pid);
  const m = useMemo(() => (t.data ? mask(t.data, filters, undefined, res) : null), [t.data, filters, res]);
  const sel = useMemo(() => {
    if (!t.data || !m) return null;
    let n = 0, tiv = 0;
    for (let i = 0; i < t.data.n; i++) if (m[i]) { n++; tiv += t.data.tiv[i]!; }
    return { n, tiv };
  }, [t.data, m]);

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>{meta?.name ?? "Exposure"}</h1>
          {meta && <p className="muted">{fmtInt(meta.summary.n_locations)} locations · {fmtInt(meta.summary.n_accounts)} accounts · seed {meta.config.seed} · OED v2 subset</p>}
        </div>
        <button className={showForm ? "" : "primary"} onClick={() => setShowForm(!showForm)}>{showForm ? "Close" : "New portfolio"}</button>
      </div>
      {(showForm || (portfolios.data && !portfolios.data.length)) && (
        <Panel title="Generate synthetic OED portfolio" subtitle="Clark-law hubs, regional construction mixes, heavy-tailed commercial schedules — fully reproducible from config + seed.">
          <GenerateForm onDone={(id) => { setShowForm(false); setPortfolio(id); }} />
        </Panel>
      )}
      {!pid && portfolios.data?.length ? <Empty title="Select a portfolio">Use the selector in the top bar or ⌘K.</Empty> : null}
      {meta && (
        <div className="kpis">
          <div className="kpi"><span className="kpi-label">Total TIV</span><span className="kpi-value">{fmtMoney(meta.summary.tiv_total)}</span><span className="kpi-sub">building {((meta.summary.tiv_building / meta.summary.tiv_total) * 100).toFixed(0)}%</span></div>
          {meta.summary.by_peril.map((p) => (
            <div className="kpi" key={p.peril}><span className="kpi-label">{codes?.perils[p.peril] ?? p.peril}</span><span className="kpi-value">{fmtMoney(p.tiv)}</span><span className="kpi-sub">{fmtInt(p.n_locations)} locations</span></div>
          ))}
          <div className="kpi"><span className="kpi-label">In current filter</span><span className="kpi-value">{sel ? fmtMoney(sel.tiv) : "–"}</span><span className="kpi-sub">{sel ? `${fmtInt(sel.n)} locations` : " "}</span></div>
        </div>
      )}
      {t.error && <p className="error">{t.error}</p>}
      {t.data && (
        <div className="grid">
          <Panel className="span-8" title="TIV density" actions={<Segmented label="Hex resolution" value={res} onChange={setRes} options={[{ value: "5", label: "H3 r5" }, { value: "7", label: "H3 r7" }]} />}
                 subtitle="Click a hexagon to filter every widget to that cell.">
            <HexMap table={t.data} mode={mode} metric="tiv" res={res} height={460}
                    legend={<RampLegend label="TIV per cell" stops={BLUE_RAMP.slice(3)} lo="low" hi="high (√ scale)" />} />
          </Panel>
          <Panel className="span-4" title="TIV by hub"><Breakdown table={t.data} dim="hub" metric="tiv" mode={mode} codes={codes} /></Panel>
          <Panel className="span-4" title="TIV by occupancy"><Breakdown table={t.data} dim="occupancy" metric="tiv" mode={mode} codes={codes} /></Panel>
          <Panel className="span-4" title="TIV by construction"><Breakdown table={t.data} dim="construction" metric="tiv" mode={mode} codes={codes} /></Panel>
          <Panel className="span-4" title="Locations by decade built">{m && <YearBuilt years={t.data.year} m={m} mode={mode} />}</Panel>
          <Panel className="span-6" title="TIV: peril › hub › occupancy" subtitle="Click to zoom; click a hub tile to filter."><Treemap table={t.data} mode={mode} codes={codes} metric="tiv" /></Panel>
          <Panel className="span-6" title="Hub × construction (TIV)" subtitle="Click a cell to filter on both."><Heatmap table={t.data} mode={mode} codes={codes} metric="tiv" /></Panel>
        </div>
      )}
    </div>
  );
}
