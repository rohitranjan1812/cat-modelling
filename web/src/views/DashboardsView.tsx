import { useEffect, useState } from "react";
import { api, unwrap, type Dashboard, type DashboardIn, type Widget } from "../api/client";
import { Empty, Panel } from "../components/ui";
import { Breakdown, EpChart, EventsTable, Heatmap, HexMap, KpiStrip, RpTable, Treemap } from "../components/widgets";
import type { Dim } from "../lib/columns";
import { useAsync, useCodes, useLocTable, usePortfolios, useRuns } from "../lib/hooks";
import { useMode, useStore } from "../state/store";
import { useEp } from "./RiskView";

const ROW_PX = 64;
const TYPES: Widget["type"][] = ["kpi", "ep", "metrics", "map", "breakdown", "treemap", "heatmap", "events"];

function WidgetBody({ w, runId }: { w: Widget; runId: string }) {
  const mode = useMode();
  const codes = useCodes();
  const perspective = useStore((s) => s.perspective);
  const pid = useStore((s) => s.portfolioId);
  const runs = useRuns(pid);
  const portfolios = usePortfolios();
  const run = runs.data?.find((r) => r.id === runId);
  const t = useLocTable("run", run?.status === "done" ? runId : null);
  const { ep, effective } = useEp(run?.status === "done" ? runId : null, perspective);
  const h = w.h * ROW_PX - 56;
  if (!run) return null;
  switch (w.type) {
    case "kpi":
      return <KpiStrip ep={ep.data} run={run} perspective={effective} tiv={portfolios.data?.find((p) => p.id === pid)?.summary.tiv_total} />;
    case "ep":
      return <EpChart ep={ep.data} mode={mode} loading={ep.loading} />;
    case "metrics":
      return <RpTable ep={ep.data} />;
    case "events":
      return <EventsTable runId={runId} limit={Number(w.params?.limit ?? 10)} />;
    default:
      if (!t.data) return <p className="muted small">Loading…</p>;
      if (w.type === "map") return <HexMap table={t.data} mode={mode} metric="aal" res={String(w.params?.res ?? "5")} height={h} />;
      if (w.type === "breakdown") return <Breakdown table={t.data} dim={(w.params?.by as Dim) ?? "hub"} metric="aal" mode={mode} codes={codes} height={h} />;
      if (w.type === "treemap") return <Treemap table={t.data} mode={mode} codes={codes} height={h} />;
      if (w.type === "heatmap") return <Heatmap table={t.data} mode={mode} codes={codes} height={h} />;
      return null;
  }
}

export function DashboardsView() {
  const runId = useStore((s) => s.runId);
  const toast = useStore((s) => s.toast);
  const [v, setV] = useState(0);
  const list = useAsync(() => unwrap(api.GET("/api/v1/dashboards")), [v]);
  const [sel, setSel] = useState<string | null>(null);
  const [edit, setEdit] = useState(false);
  const [draft, setDraft] = useState("");
  const current: Dashboard | undefined = list.data?.find((d) => d.id === sel) ?? list.data?.[0];
  useEffect(() => {
    if (current) setDraft(JSON.stringify({ name: current.name, description: current.description, run_id: current.run_id, widgets: current.widgets }, null, 2));
  }, [current?.id, edit]);

  const create = async () => {
    const tpl = await unwrap(api.GET("/api/v1/dashboards/template"));
    const d = await unwrap(api.POST("/api/v1/dashboards", { body: { ...tpl, run_id: runId } }));
    setSel(d.id);
    setV((x) => x + 1);
  };
  const save = async () => {
    if (!current) return;
    try {
      const body = JSON.parse(draft) as DashboardIn;
      await unwrap(api.PUT("/api/v1/dashboards/{did}", { params: { path: { did: current.id } }, body }));
      setEdit(false);
      setV((x) => x + 1);
    } catch (e) {
      toast({ tone: "critical", title: "Dashboard spec invalid", body: String((e as Error).message) });
    }
  };
  const addWidget = (type: Widget["type"]) => {
    const spec = JSON.parse(draft) as DashboardIn;
    spec.widgets ??= [];
    const y = Math.max(0, ...spec.widgets.map((w) => (w.y ?? 0) + (w.h ?? 4)));
    spec.widgets.push({ type, title: type, params: type === "breakdown" ? { by: "occupancy" } : {}, x: 0, y, w: 6, h: 4 } as Widget);
    setDraft(JSON.stringify(spec, null, 2));
  };
  const remove = async () => {
    if (!current) return;
    await api.DELETE("/api/v1/dashboards/{did}", { params: { path: { did: current.id } } });
    setSel(null);
    setV((x) => x + 1);
  };
  const boardRun = current?.run_id ?? runId;

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>Dashboards</h1>
          <p className="muted">Declarative layouts: a dashboard is a JSON spec of widgets on a 12-column grid, stored server-side and bound to a run.</p>
        </div>
        <div className="row-gap">
          <select aria-label="Dashboard" value={current?.id ?? ""} onChange={(e) => setSel(e.target.value)}>
            {(list.data ?? []).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
          <button onClick={create}>New from template</button>
          {current && <button onClick={() => setEdit(!edit)}>{edit ? "Cancel" : "Edit spec"}</button>}
          {current && <button className="ghost" onClick={remove}>Delete</button>}
        </div>
      </div>
      {!current ? <Empty title="No dashboards yet"><button className="primary" onClick={create}>Create from template</button></Empty> : (
        <>
          {edit && (
            <Panel title="Spec editor" actions={<button className="primary" onClick={save}>Save</button>}>
              <div className="row-gap">{TYPES.map((t) => <button key={t} className="ghost" onClick={() => addWidget(t)}>+ {t}</button>)}</div>
              <textarea className="code" rows={18} spellCheck={false} value={draft} onChange={(e) => setDraft(e.target.value)} />
            </Panel>
          )}
          {!boardRun ? <Empty title="Bind a run">Select a run in the top bar; it will be used for this dashboard.</Empty> : (
            <div className="dash-grid" style={{ gridAutoRows: `${ROW_PX}px` }}>
              {(current.widgets ?? []).map((w) => (
                <section key={w.id} className="panel dash-cell"
                  style={{ gridColumn: `${(w.x ?? 0) + 1} / span ${w.w ?? 6}`, gridRow: `${(w.y ?? 0) + 1} / span ${w.h ?? 4}` }}>
                  <header className="panel-head"><h2>{w.title || w.type}</h2></header>
                  <div className="dash-body"><WidgetBody w={w} runId={boardRun} /></div>
                </section>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
