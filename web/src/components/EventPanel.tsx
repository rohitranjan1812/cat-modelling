import { useMemo, useState } from "react";
import type { CodeLists, EventDrill } from "../api/client";
import { fmtInt, fmtMoney, fmtPct, fmtRate } from "../lib/format";
import { codeLabel } from "../lib/hooks";
import { PERIL_LABEL, perilColor, type Mode } from "../lib/palette";
import { axisStyle, baseOption, Chart, chrome } from "./Chart";


const DED_TYPE: Record<number, string> = { 0: "", 1: "% loss", 2: "% TIV" };

/** Distribution of sampled event losses: secondary uncertainty made visible. */
function SampleHistogram({ samples, mode }: { samples: number[]; mode: Mode }) {
  const option = useMemo(() => {
    const c = chrome(mode);
    const max = Math.max(...samples, 1);
    const nb = Math.min(24, Math.max(6, Math.round(Math.sqrt(samples.length))));
    const w = max / nb;
    const counts = new Array(nb).fill(0);
    samples.forEach((s) => counts[Math.min(nb - 1, Math.floor(s / w))]++);
    return {
      ...baseOption(mode),
      grid: { left: 40, right: 12, top: 8, bottom: 28 },
      tooltip: { ...(baseOption(mode).tooltip as object), trigger: "item",
        formatter: (p: { dataIndex: number; value: number }) =>
          `<b>${p.value} samples</b><br/>${fmtMoney(p.dataIndex * w)} – ${fmtMoney((p.dataIndex + 1) * w)}` },
      xAxis: { type: "category", data: counts.map((_, i) => fmtMoney((i + 0.5) * w, "")), ...axisStyle(mode),
               axisLabel: { color: c.muted, fontSize: 10, interval: Math.ceil(nb / 5) - 1 } },
      yAxis: { type: "value", minInterval: 1, ...axisStyle(mode) },
      series: [{ type: "bar", data: counts, barCategoryGap: "8%", itemStyle: { color: mode === "dark" ? "#3987e5" : "#2a78d6", borderRadius: [4, 4, 0, 0] } }],
    };
  }, [samples, mode]);
  return <Chart option={option} height={150} ariaLabel="Histogram of sampled event gross losses" />;
}

export function EventPanel({ d, mode, codes, onClose, extra }: {
  d: EventDrill; mode: Mode; codes?: CodeLists; onClose?: () => void; extra?: React.ReactNode;
}) {
  const [persp, setPersp] = useState<"gu" | "gross">("gross");
  const st = d[persp];
  const samples = persp === "gu" ? d.samples_gu : d.samples_gross;
  return (
    <aside className="event-panel" aria-label="Event drill-down">
      <header className="event-head">
        <div>
          <div className="eyebrow">
            <span className="dot" style={{ background: perilColor(d.peril, mode) }} />
            {PERIL_LABEL[d.peril] ?? d.peril}{d.event_id > 0 && <> · #{d.event_id}</>}
            {d.rate > 0 && <> · {fmtRate(d.rate)}</>}
          </div>
          <h3>{d.name}</h3>
        </div>
        {onClose && <button className="ghost" onClick={onClose} aria-label="Close event">✕</button>}
      </header>
      {extra}
      <div className="seg-row">
        <div className="segmented small" role="radiogroup" aria-label="Loss perspective">
          {(["gross", "gu"] as const).map((p) => (
            <button key={p} className={p === persp ? "seg active" : "seg"} role="radio" aria-checked={p === persp} onClick={() => setPersp(p)}>
              {p === "gu" ? "Ground-up" : "Gross"}
            </button>
          ))}
        </div>
        <span className="muted small">{fmtInt(d.n_affected)} locations{samples.length > 0 && <> · {samples.length} samples</>}</span>
      </div>
      <div className="mini-kpis">
        <div><span>Mean</span><b>{fmtMoney(st.mean)}</b></div>
        <div><span>P50</span><b>{fmtMoney(st.p50)}</b></div>
        <div><span>P90</span><b>{fmtMoney(st.p90)}</b></div>
        <div><span>P99</span><b>{fmtMoney(st.p99)}</b></div>
        <div><span>CoV</span><b>{st.mean ? fmtPct(st.sd / st.mean, 0) : "–"}</b></div>
      </div>
      {samples.length >= 8 && <SampleHistogram samples={samples} mode={mode} />}
      {d.by_hub.length > 0 && (
        <>
          <h4>By hub</h4>
          <table className="table compact">
            <tbody>
              {d.by_hub.map((h) => (
                <tr key={h.key}>
                  <td>{h.key}</td>
                  <td className="num muted">{fmtInt(h.n)}</td>
                  <td className="num">{fmtMoney(persp === "gu" ? h.gu : h.gross)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <h4>Top locations <span className="muted small">(mean over samples · intensity in {d.unit})</span></h4>
      <div className="scroll-x">
        <table className="table compact">
          <thead>
            <tr><th>Location</th><th>Class</th><th className="num">TIV</th><th className="num">Intensity</th>
                <th className="num">GU</th><th className="num">Gross</th><th>Terms</th></tr>
          </thead>
          <tbody>
            {d.top_locations.map((l) => (
              <tr key={l.loc_number}>
                <td title={`${l.acc_number} · ${l.lat.toFixed(3)}, ${l.lon.toFixed(3)}`}>{l.loc_number}<div className="muted small">{l.hub}</div></td>
                <td className="small">{codeLabel(codes, "occupancy", String(l.occupancy))}<div className="muted">{codeLabel(codes, "construction", String(l.construction))} · {l.year_built}</div></td>
                <td className="num">{fmtMoney(l.tiv, "")}</td>
                <td className="num">{l.intensity.toFixed(d.peril === "QEQ" ? 2 : 0)}</td>
                <td className="num">{fmtMoney(l.gu, "")}</td>
                <td className="num">{fmtMoney(l.gross, "")}</td>
                <td className="small muted">
                  ded {l.loc_ded_type === 0 ? fmtMoney(l.loc_ded, "") : `${(l.loc_ded * 100).toFixed(0)}${DED_TYPE[l.loc_ded_type]}`}
                  {l.loc_limit > 0 && <> · lim {fmtMoney(l.loc_limit, "")}</>}
                  <br />layer {fmtMoney(l.layer_limit, "")} xs {fmtMoney(l.layer_attachment, "")} @ {fmtPct(l.layer_participation, 0)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">All amounts in USD.</p>
    </aside>
  );
}
