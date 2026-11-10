/**
 * Reusable analytic widgets. All of them read the same filter state, so every chart, tile, map
 * and table on a page re-renders against one slice and the numbers always agree.
 */
import { useMemo } from "react";
import type { ECElementEvent } from "echarts/core";
import { api, unwrap, type CodeLists, type EPResponse, type Perspective, type RunMeta } from "../api/client";
import { aalVector, groupBy, keyFn, mask, type Dim, type Filters, type LocTable } from "../lib/columns";
import { fmtInt, fmtMoney, fmtPct, fmtRate, fmtRp } from "../lib/format";
import { codeLabel, useAsync } from "../lib/hooks";
import { hexLayer, geometryLayer, type HexDatum } from "../lib/layers";
import { BLUE_RAMP, ORANGE_RAMP, PERIL_LABEL, perilColor, ramp, rgb, scaler, type Mode } from "../lib/palette";
import { useStore } from "../state/store";
import { axisStyle, baseOption, Chart, chrome } from "./Chart";
import { MapView, type BBox } from "./MapView";

export const SERIES2: Record<Mode, [string, string]> = {
  light: ["#2a78d6", "#1baf7a"], // OEP blue, AEP aqua (validated pair)
  dark: ["#3987e5", "#199e70"],
};

// ------------------------------------------------------------------------------ KPI strip
export function KpiStrip({ ep, run, perspective, tiv }: { ep?: EPResponse; run: RunMeta; perspective: Perspective; tiv?: number }) {
  const m = ep?.metrics;
  const ccy = "USD";
  const se = (v?: number | null) => (v ? `± ${fmtMoney(v, "")}` : "");
  const tiles: { label: string; value: string; sub?: string }[] = [
    { label: `AAL (${perspective})`, value: fmtMoney(m?.aal, ccy), sub: se(m?.aal_se) },
    { label: "Loss cost", value: m && tiv ? `${((m.aal / tiv) * 1000).toFixed(2)}‰` : "–", sub: "AAL / TIV" },
    { label: "OEP 1-in-100", value: fmtMoney(m?.oep["100"], ccy), sub: se(m?.oep_se["100"]) },
    { label: "OEP 1-in-250", value: fmtMoney(m?.oep["250"], ccy), sub: se(m?.oep_se["250"]) },
    { label: "TVaR 99%", value: fmtMoney(m?.tvar["0.99"], ccy), sub: se(m?.tvar_se["0.99"]) },
    { label: "Simulated years", value: fmtInt(run.config.n_years), sub: `${run.config.n_samples} samples / event` },
  ];
  return (
    <div className={`kpis ${ep ? "" : "refetching"}`}>
      {tiles.map((t) => (
        <div className="kpi" key={t.label}>
          <span className="kpi-label">{t.label}</span>
          <span className="kpi-value">{t.value}</span>
          <span className="kpi-sub">{t.sub || " "}</span>
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------------------ EP curve
export function EpChart({ ep, mode, selectedRp, onPick, loading }: {
  ep?: EPResponse; mode: Mode; selectedRp?: number | null; onPick?: (rp: number) => void; loading?: boolean;
}) {
  const option = useMemo(() => {
    const c = chrome(mode);
    const [cOep, cAep] = SERIES2[mode];
    const pts = (ep?.curve ?? []).filter((p) => p.rp >= 1.5);
    const last = pts[pts.length - 1];
    const line = (name: string, key: "oep" | "aep", color: string) => ({
      name,
      type: "line",
      showSymbol: false,
      symbolSize: 8,
      lineStyle: { width: 2, color },
      itemStyle: { color },
      emphasis: { focus: "series" },
      data: pts.map((p) => [p.rp, p[key]]),
      endLabel: { show: !!last, formatter: name, color: c.secondary, fontSize: 11 },
      markLine: selectedRp && key === "oep"
        ? { symbol: "none", silent: true, label: { formatter: `1-in-${fmtRp(selectedRp)}`, color: c.secondary },
            lineStyle: { color: c.muted, width: 1, type: "solid" }, data: [{ xAxis: selectedRp }] }
        : undefined,
    });
    return {
      ...baseOption(mode),
      grid: { left: 64, right: 56, top: 28, bottom: 40 },
      legend: { top: 0, left: 56, textStyle: { color: c.secondary }, icon: "path://M0,4 L14,4", itemWidth: 14 },
      tooltip: {
        ...(baseOption(mode).tooltip as object),
        trigger: "axis",
        axisPointer: { type: "line", lineStyle: { color: c.muted } },
        valueFormatter: (v: number) => fmtMoney(v),
      },
      xAxis: { type: "log", name: "Return period (years)", nameLocation: "middle", nameGap: 26, min: 1.5,
               ...axisStyle(mode), axisLabel: { ...axisStyle(mode).axisLabel, formatter: (v: number) => fmtRp(v) } },
      yAxis: { type: "value", ...axisStyle(mode), axisLabel: { ...axisStyle(mode).axisLabel, formatter: (v: number) => fmtMoney(v, "") } },
      series: [line("OEP", "oep", cOep), line("AEP", "aep", cAep)],
    };
  }, [ep, mode, selectedRp]);
  return (
    <Chart
      option={option}
      ariaLabel="Exceedance probability curves (OEP and AEP) against return period"
      loading={loading}
      onClick={(p: ECElementEvent) => {
        const d = p.data as [number, number] | undefined;
        if (d && onPick) onPick(d[0]);
      }}
    />
  );
}

export function RpTable({ ep }: { ep?: EPResponse }) {
  const m = ep?.metrics;
  if (!m) return null;
  const rps = Object.keys(m.oep);
  return (
    <table className="table compact">
      <thead>
        <tr><th>Return period</th><th className="num">OEP</th><th className="num">± SE</th><th className="num">AEP</th><th className="num">± SE</th></tr>
      </thead>
      <tbody>
        {rps.map((r) => (
          <tr key={r}>
            <td>1-in-{r}</td>
            <td className="num">{fmtMoney(m.oep[r])}</td>
            <td className="num muted">{m.oep_se[r] ? fmtMoney(m.oep_se[r], "") : "–"}</td>
            <td className="num">{fmtMoney(m.aep[r])}</td>
            <td className="num muted">{m.aep_se[r] ? fmtMoney(m.aep_se[r], "") : "–"}</td>
          </tr>
        ))}
        {Object.keys(m.tvar).map((lv) => (
          <tr key={lv}>
            <td>TVaR {fmtPct(Number(lv))}</td>
            <td className="num" colSpan={2}>VaR {fmtMoney(m.var[lv])}</td>
            <td className="num">{fmtMoney(m.tvar[lv])}</td>
            <td className="num muted">{m.tvar_se[lv] ? fmtMoney(m.tvar_se[lv], "") : "–"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ------------------------------------------------------------------------------ breakdown bars
const FILTER_KEY: Record<Dim, keyof Filters> = {
  hub: "hubs", occupancy: "occupancies", construction: "constructions", peril: "perils", h3: "h3",
};

export function metricVector(t: LocTable, metric: "aal" | "tiv", perspective: Perspective, perils: string[]) {
  if (metric === "tiv") return null;
  return aalVector(t, perspective === "gu" ? "gu" : "gross", perils);
}

export function Breakdown({ table, dim, metric, mode, codes, limit = 12, height }: {
  table: LocTable; dim: Dim; metric: "aal" | "tiv"; mode: Mode; codes?: CodeLists; limit?: number; height?: number;
}) {
  const filters = useStore((s) => s.filters);
  const perspective = useStore((s) => s.perspective);
  const toggle = useStore((s) => s.toggle);
  const groups = useMemo(() => {
    const m = mask(table, filters, dim);
    if (dim === "peril") {
      // AAL by peril from per-peril columns (a location may cover several perils)
      const perils = [...new Set(table.perils.labels.flatMap((l) => l.split(";")).filter(Boolean))];
      return perils.map((p) => {
        const v = metric === "tiv" ? null : aalVector(table, perspective === "gu" ? "gu" : "gross", [p]);
        let sum = 0, n = 0;
        const has = table.perils.labels.map((l) => l.split(";").includes(p));
        for (let i = 0; i < table.n; i++) if (m[i] && has[table.perils.codes[i]!]) { sum += v ? v[i]! : table.tiv[i]!; n++; }
        return { key: p, n, tiv: 0, value: sum, lat: 0, lon: 0 };
      }).sort((a, b) => b.value - a.value);
    }
    return groupBy(table, keyFn(table, dim), m, metricVector(table, metric, perspective, filters.perils));
  }, [table, filters, dim, metric, perspective]);
  const selected = filters[FILTER_KEY[dim]] as (string | number)[];
  const shown = groups.slice(0, limit);
  const option = useMemo(() => {
    const c = chrome(mode);
    const base = mode === "dark" ? "#3987e5" : "#2a78d6";
    const dimmed = mode === "dark" ? "#184f95" : "#b7d3f6";
    const keyOf = (k: string) => (dim === "occupancy" || dim === "construction" ? Number(k) : k);
    const rows = [...shown].reverse();
    return {
      ...baseOption(mode),
      grid: { left: 8, right: 64, top: 4, bottom: 4, containLabel: true },
      tooltip: { ...(baseOption(mode).tooltip as object), trigger: "item",
        formatter: (p: { name: string; value: number; dataIndex: number }) => {
          const g = rows[p.dataIndex]!;
          return `<b>${fmtMoney(g.value)}</b><br/>${escapeHtml(p.name)}<br/>${fmtInt(g.n)} locations`;
        } },
      xAxis: { type: "value", show: false },
      yAxis: { type: "category", data: rows.map((g) => codeLabel(codes, dim, g.key)), ...axisStyle(mode),
               axisLine: { show: false }, axisLabel: { color: c.secondary, fontSize: 11, width: 130, overflow: "truncate" } },
      series: [{
        type: "bar",
        barMaxWidth: 16,
        barCategoryGap: "30%",
        itemStyle: { borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: "right", color: c.secondary, fontSize: 11, formatter: (p: { value: number }) => fmtMoney(p.value, "") },
        data: rows.map((g) => {
          const isSel = selected.includes(keyOf(g.key));
          const col = dim === "peril" ? perilColor(g.key, mode) : base;
          return { value: g.value, itemStyle: { color: selected.length && !isSel ? (dim === "peril" ? `${col}66` : dimmed) : col } };
        }),
      }],
    };
  }, [shown, mode, codes, dim, selected]);
  return (
    <Chart
      option={option}
      height={height ?? Math.max(120, shown.length * 26 + 16)}
      ariaLabel={`${metric.toUpperCase()} by ${dim}`}
      onClick={(p) => {
        const g = [...shown].reverse()[p.dataIndex];
        if (g) toggle(dim, dim === "occupancy" || dim === "construction" ? Number(g.key) : g.key);
      }}
    />
  );
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]!);
}

// ------------------------------------------------------------------------------ treemap
export function Treemap({ table, mode, codes, metric = "aal", height = 300 }: {
  table: LocTable; mode: Mode; codes?: CodeLists; metric?: "aal" | "tiv"; height?: number;
}) {
  const filters = useStore((s) => s.filters);
  const perspective = useStore((s) => s.perspective);
  const toggle = useStore((s) => s.toggle);
  const option = useMemo(() => {
    const m = mask(table, { ...filters, perils: [] });
    const perils = [...new Set(table.perils.labels.flatMap((l) => l.split(";")).filter(Boolean))]
      .filter((p) => !filters.perils.length || filters.perils.includes(p));
    const children = perils.map((p) => {
      const v = metric === "tiv" ? table.tiv : aalVector(table, perspective === "gu" ? "gu" : "gross", [p])!;
      const has = table.perils.labels.map((l) => l.split(";").includes(p));
      const byHub = new Map<string, Map<number, number>>();
      for (let i = 0; i < table.n; i++) {
        if (!m[i] || !has[table.perils.codes[i]!]) continue;
        const h = table.hub.labels[table.hub.codes[i]!]!;
        const occ = byHub.get(h) ?? new Map<number, number>();
        occ.set(table.occ[i]!, (occ.get(table.occ[i]!) ?? 0) + v[i]!);
        byHub.set(h, occ);
      }
      const color = perilColor(p, mode);
      return {
        name: PERIL_LABEL[p] ?? p,
        itemStyle: { color },
        children: [...byHub].map(([h, occ]) => ({
          name: h,
          children: [...occ].map(([o, val]) => ({ name: codeLabel(codes, "occupancy", String(o)), value: val })),
        })),
      };
    });
    const c = chrome(mode);
    return {
      ...baseOption(mode),
      tooltip: { ...(baseOption(mode).tooltip as object), formatter: (p: { name: string; value: number; treePathInfo: { name: string }[] }) =>
        `<b>${fmtMoney(p.value)}</b><br/>${p.treePathInfo.slice(1).map((x) => escapeHtml(x.name)).join(" › ")}` },
      series: [{
        type: "treemap",
        roam: false,
        nodeClick: "zoomToNode",
        breadcrumb: { show: false },
        width: "100%",
        height: "100%",
        top: 0,
        leafDepth: 2,
        label: { color: "#ffffff", fontSize: 11 },
        upperLabel: { show: true, height: 18, color: "#ffffff", fontSize: 11 },
        itemStyle: { borderColor: c.surface, borderWidth: 2, gapWidth: 2 },
        levels: [
          { itemStyle: { borderWidth: 0, gapWidth: 2 } },
          { colorSaturation: [0.35, 0.6], itemStyle: { gapWidth: 2, borderColorSaturation: 0.6 } },
          { colorSaturation: [0.3, 0.55], itemStyle: { gapWidth: 1 } },
        ],
        data: children,
      }],
    };
  }, [table, filters, perspective, mode, codes, metric]);
  return (
    <Chart option={option} height={height} ariaLabel="Treemap of AAL by peril, hub and occupancy"
      onClick={(p) => {
        const path = (p as unknown as { treePathInfo?: { name: string }[] }).treePathInfo;
        if (path && path.length === 3) toggle("hub", path[2]!.name);
      }} />
  );
}

// ------------------------------------------------------------------------------ heatmap
export function Heatmap({ table, mode, codes, metric = "aal", height = 300 }: {
  table: LocTable; mode: Mode; codes?: CodeLists; metric?: "aal" | "tiv"; height?: number;
}) {
  const filters = useStore((s) => s.filters);
  const perspective = useStore((s) => s.perspective);
  const { setFilter } = useStore.getState();
  const { option, hubs, cons } = useMemo(() => {
    const m = mask(table, { ...filters, hubs: [], constructions: [] });
    const v = metricVector(table, metric, perspective, filters.perils);
    const cell = new Map<string, number>();
    const hubTot = new Map<string, number>();
    const consSet = new Set<number>();
    for (let i = 0; i < table.n; i++) {
      if (!m[i]) continue;
      const h = table.hub.labels[table.hub.codes[i]!]!;
      const k = `${h}|${table.cons[i]}`;
      const val = v ? v[i]! : table.tiv[i]!;
      cell.set(k, (cell.get(k) ?? 0) + val);
      hubTot.set(h, (hubTot.get(h) ?? 0) + val);
      consSet.add(table.cons[i]!);
    }
    const hubs = [...hubTot].sort((a, b) => a[1] - b[1]).map(([h]) => h);
    const cons = [...consSet].sort();
    const data = hubs.flatMap((h, y) => cons.map((c, x) => [x, y, cell.get(`${h}|${c}`) ?? 0]));
    const max = Math.max(1, ...data.map((d) => d[2] as number));
    const c = chrome(mode);
    return {
      hubs, cons,
      option: {
        ...baseOption(mode),
        grid: { left: 8, right: 8, top: 8, bottom: 56, containLabel: true },
        tooltip: { ...(baseOption(mode).tooltip as object), formatter: (p: { value: [number, number, number] }) =>
          `<b>${fmtMoney(p.value[2])}</b><br/>${escapeHtml(hubs[p.value[1]]!)} · ${escapeHtml(codeLabel(codes, "construction", String(cons[p.value[0]])))}` },
        xAxis: { type: "category", data: cons.map((k) => codeLabel(codes, "construction", String(k))), ...axisStyle(mode), splitArea: { show: false },
                 axisLabel: { color: c.muted, fontSize: 10, interval: 0, rotate: 20 } },
        yAxis: { type: "category", data: hubs, ...axisStyle(mode), axisLabel: { color: c.secondary, fontSize: 11 } },
        visualMap: { min: 0, max, calculable: false, orient: "horizontal", left: "center", bottom: 0, itemHeight: 120,
                     inRange: { color: BLUE_RAMP }, textStyle: { color: c.muted, fontSize: 10 },
                     formatter: (v: number) => fmtMoney(v, "") },
        series: [{ type: "heatmap", data, itemStyle: { borderColor: c.surface, borderWidth: 2, borderRadius: 2 },
                   emphasis: { itemStyle: { borderColor: c.text, borderWidth: 1 } } }],
      },
    };
  }, [table, filters, perspective, mode, codes, metric]);
  return (
    <Chart option={option} height={height} ariaLabel="Heatmap of hub by construction class"
      onClick={(p) => {
        const [x, y] = p.value as [number, number, number];
        setFilter("hub", [hubs[y]!]);
        setFilter("construction", [cons[x]!]);
      }} />
  );
}

// ------------------------------------------------------------------------------ map
export function HexMap({ table, mode, metric, res, height = 420, overlay, bounds, legend }: {
  table: LocTable; mode: Mode; metric: "aal" | "tiv"; res: string; height?: number | string;
  overlay?: React.ComponentProps<typeof MapView>["layers"]; bounds?: BBox | null; legend?: React.ReactNode;
}) {
  const filters = useStore((s) => s.filters);
  const perspective = useStore((s) => s.perspective);
  const toggle = useStore((s) => s.toggle);
  const { layer, fit } = useMemo(() => {
    const m = mask(table, filters, "h3", res);
    const groups = groupBy(table, keyFn(table, "h3", res), m, metricVector(table, metric, perspective, filters.perils));
    const scale = scaler(groups.map((g) => g.value));
    const data: HexDatum[] = groups.map((g) => ({
      h3: g.key, value: g.value, selected: filters.h3.includes(g.key),
      label: `${fmtMoney(g.value)} ${metric === "aal" ? "AAL" : "TIV"} · ${fmtInt(g.n)} locations`,
    }));
    const lats = groups.map((g) => g.lat), lons = groups.map((g) => g.lon);
    const fit: BBox | null = groups.length ? [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)] : null;
    return {
      fit,
      layer: hexLayer(`hex-${metric}`, data, (d) => ramp(BLUE_RAMP.slice(3), scale(d.value)), {
        onClick: (d) => toggle("h3", d.h3), outline: mode === "dark" ? [26, 26, 25] : [252, 252, 251],
      }),
    };
  }, [table, filters, metric, perspective, res, mode, toggle]);
  return (
    <MapView
      mode={mode}
      height={height}
      layers={[layer, ...(overlay ?? [])]}
      bounds={bounds ?? fit}
      tooltip={(info) => (info.object as HexDatum | undefined)?.label ?? (info.object as { label?: string } | undefined)?.label ?? null}
    >
      {legend}
    </MapView>
  );
}

export function RampLegend({ label, stops, lo, hi }: { label: string; stops: string[]; lo: string; hi: string }) {
  return (
    <div className="map-legend" aria-label={`${label} colour scale`}>
      <span className="map-legend-title">{label}</span>
      <div className="ramp" style={{ background: `linear-gradient(90deg, ${stops.join(",")})` }} />
      <div className="ramp-ends"><span>{lo}</span><span>{hi}</span></div>
    </div>
  );
}

// ------------------------------------------------------------------------------ events table
export function EventsTable({ runId, limit = 25, onOpen }: { runId: string; limit?: number; onOpen?: (id: number) => void }) {
  const perils = useStore((s) => s.filters.perils);
  const setEvent = useStore((s) => s.setEvent);
  const selected = useStore((s) => s.eventId);
  const peril = perils.length === 1 ? perils[0] : undefined;
  const r = useAsync(() => unwrap(api.GET("/api/v1/runs/{rid}/events", {
    params: { path: { rid: runId }, query: { limit, peril, sort: "mean_gross" } } })), [runId, limit, peril]);
  return (
    <div className={r.loading ? "refetching" : ""}>
      <table className="table compact clickable">
        <thead>
          <tr><th>Event</th><th>Peril</th><th className="num">Frequency</th><th className="num">Locations</th>
              <th className="num">Mean GU</th><th className="num">Mean gross</th><th className="num">Equiv. RP</th></tr>
        </thead>
        <tbody>
          {(r.data?.items ?? []).map((e) => (
            <tr key={e.event_id} className={selected === e.event_id ? "selected" : ""}
                tabIndex={0} onClick={() => (onOpen ?? setEvent)(e.event_id)}
                onKeyDown={(k) => k.key === "Enter" && (onOpen ?? setEvent)(e.event_id)}>
              <td className="truncate" title={e.name}>{e.name}</td>
              <td><span className="dot" style={{ background: perilColor(e.peril, "light") }} />{e.peril}</td>
              <td className="num">{fmtRate(e.rate)}</td>
              <td className="num">{fmtInt(e.n_affected)}</td>
              <td className="num">{fmtMoney(e.mean_gu)}</td>
              <td className="num">{fmtMoney(e.mean_gross)}</td>
              <td className="num">{e.rp_equiv ? fmtRp(e.rp_equiv) : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {r.data && <p className="muted small">{fmtInt(r.data.total)} loss-causing events in catalogue</p>}
    </div>
  );
}

// ------------------------------------------------------------------------------ helpers for event overlays
export function footprintLayers(
  cells: { h3: string; intensity: number }[],
  geometry: unknown,
  mode: Mode,
  peril: string,
) {
  const scale = scaler(cells.map((c) => c.intensity));
  const data: HexDatum[] = cells.map((c) => ({ h3: c.h3, value: c.intensity, label: `intensity ${c.intensity.toFixed(peril === "QEQ" ? 3 : 1)}` }));
  return [
    hexLayer("footprint", data, (d) => ramp(ORANGE_RAMP, 0.15 + 0.85 * scale(d.value)), { opacity: 0.75 }),
    geometryLayer("event-geom", geometry, rgb(mode === "dark" ? "#f0efec" : "#0b0b0b"), 3),
  ];
}
