import { useEffect } from "react";
import { CommandPalette } from "./components/CommandPalette";
import { Status } from "./components/ui";
import { activeCount } from "./lib/columns";
import { codeLabel, useCodes, usePortfolios, useRuns } from "./lib/hooks";
import { useMode, useStore, VIEWS, type View } from "./state/store";
import { AnalysesView } from "./views/AnalysesView";
import { DashboardsView } from "./views/DashboardsView";
import { ExposureView } from "./views/ExposureView";
import { LiveView } from "./views/LiveView";
import { RiskView } from "./views/RiskView";
import { ScenarioView } from "./views/ScenarioView";

const ICONS: Record<View, string> = {
  exposure: "M3 21h18M5 21V9l7-5 7 5v12M9 21v-6h6v6",
  risk: "M3 17l5-5 4 4 8-9M14 7h6v6",
  scenario: "M12 2v4M12 18v4M2 12h4M18 12h4M12 8a4 4 0 100 8 4 4 0 000-8z",
  live: "M2 12h4l3-8 4 16 3-8h6",
  dashboards: "M3 3h8v10H3zM13 3h8v6h-8zM13 11h8v10h-8zM3 15h8v6H3z",
  analyses: "M4 6h16M4 12h10M4 18h6M18 14l3 3-3 3",
};

function Icon({ d }: { d: string }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d} />
    </svg>
  );
}

function FilterRow() {
  const filters = useStore((s) => s.filters);
  const { toggle, clearFilters } = useStore.getState();
  const codes = useCodes();
  const chips: { dim: Parameters<typeof toggle>[0]; v: string | number; label: string }[] = [
    ...filters.perils.map((v) => ({ dim: "peril" as const, v, label: codeLabel(codes, "peril", v) })),
    ...filters.hubs.map((v) => ({ dim: "hub" as const, v, label: v })),
    ...filters.occupancies.map((v) => ({ dim: "occupancy" as const, v, label: codeLabel(codes, "occupancy", String(v)) })),
    ...filters.constructions.map((v) => ({ dim: "construction" as const, v, label: codeLabel(codes, "construction", String(v)) })),
    ...filters.h3.map((v) => ({ dim: "h3" as const, v, label: `cell ${v.slice(0, 7)}…` })),
  ];
  return (
    <div className="filter-row" aria-label="Active filters">
      <span className="muted small">Filters</span>
      {(["QEQ", "WTC"] as const).map((p) => (
        <button key={p} className={`chip ${filters.perils.includes(p) ? "on" : ""}`} aria-pressed={filters.perils.includes(p)} onClick={() => toggle("peril", p)}>
          <span className={`dot peril-${p}`} />{codeLabel(codes, "peril", p)}
        </button>
      ))}
      {chips.filter((c) => c.dim !== "peril").map((c) => (
        <button key={`${c.dim}:${c.v}`} className="chip on" onClick={() => toggle(c.dim, c.v)} aria-label={`Remove filter ${c.label}`}>
          <span className="muted">{c.dim}</span> {c.label} <span aria-hidden>✕</span>
        </button>
      ))}
      {activeCount(filters) > 0 && <button className="link small" onClick={clearFilters}>Clear all</button>}
      {activeCount(filters) === 0 && <span className="muted small">— click any bar, hexagon or cell to cross-filter</span>}
    </div>
  );
}

function TopBar() {
  const { portfolioId, runId, perspective, ws, theme, view } = useStore();
  const { setPortfolio, setRun, setPerspective, setTheme, setPalette } = useStore.getState();
  const portfolios = usePortfolios();
  const runs = useRuns(portfolioId);
  useEffect(() => {
    // Auto-select the most recent portfolio / finished run so the app is never empty.
    if (!portfolioId && portfolios.data?.length) setPortfolio(portfolios.data[0]!.id);
  }, [portfolios.data, portfolioId, setPortfolio]);
  useEffect(() => {
    if (!runId && runs.data?.length) {
      const done = runs.data.find((r) => r.status === "done") ?? runs.data[0];
      if (done) setRun(done.id);
    }
  }, [runs.data, runId, setRun]);
  const showRun = view !== "exposure" && view !== "scenario" && view !== "live";
  return (
    <header className="topbar">
      <strong className="brand">catmod</strong>
      <select aria-label="Portfolio" value={portfolioId ?? ""} onChange={(e) => setPortfolio(e.target.value || null)}>
        {!portfolios.data?.length && <option value="">No portfolios</option>}
        {portfolios.data?.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.summary.n_locations.toLocaleString()}</option>)}
      </select>
      {showRun && (
        <select aria-label="Analysis run" value={runId ?? ""} onChange={(e) => setRun(e.target.value || null)}>
          {!runs.data?.length && <option value="">No runs</option>}
          {runs.data?.map((r) => <option key={r.id} value={r.id}>{r.name}{r.status !== "done" ? ` (${r.status})` : ""}</option>)}
        </select>
      )}
      {showRun && (
        <div className="segmented" role="radiogroup" aria-label="Loss perspective">
          {(["gu", "gross", "net"] as const).map((p) => (
            <button key={p} role="radio" aria-checked={perspective === p} className={perspective === p ? "seg active" : "seg"} onClick={() => setPerspective(p)}>
              {p === "gu" ? "GU" : p === "gross" ? "Gross" : "Net"}
            </button>
          ))}
        </div>
      )}
      <span className="spacer" />
      <button className="ghost kbd-btn" onClick={() => setPalette(true)} aria-label="Open command palette">Search <kbd>⌘K</kbd></button>
      <button className="ghost" aria-label="Toggle theme" onClick={() => setTheme(theme === "dark" ? "light" : theme === "light" ? "system" : "dark")}>
        {theme === "dark" ? "Dark" : theme === "light" ? "Light" : "Auto"}
      </button>
      <span className={`ws ws-${ws}`} title={`Live connection: ${ws}`} aria-label={`Live connection ${ws}`} />
    </header>
  );
}

export function App() {
  const view = useStore((s) => s.view);
  const theme = useStore((s) => s.theme);
  const toasts = useStore((s) => s.toasts);
  const { setView, setPalette, clearFilters, dismiss } = useStore.getState();
  useMode();

  useEffect(() => {
    if (theme === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const typing = /INPUT|TEXTAREA|SELECT/.test(target.tagName);
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette(!useStore.getState().paletteOpen);
      } else if (!typing && !e.metaKey && !e.ctrlKey && !e.altKey) {
        const v = VIEWS.find((x) => x.key === e.key);
        if (v) setView(v.id);
        if (e.key === "Escape") clearFilters();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setPalette, setView, clearFilters]);

  return (
    <div className="app">
      <nav className="rail" aria-label="Views">
        {VIEWS.map((v) => (
          <button key={v.id} className={view === v.id ? "rail-item active" : "rail-item"} onClick={() => setView(v.id)}
                  aria-current={view === v.id ? "page" : undefined} title={`${v.label} (${v.key})`}>
            <Icon d={ICONS[v.id]} />
            <span>{v.label}</span>
          </button>
        ))}
      </nav>
      <div className="main">
        <TopBar />
        {view !== "analyses" && view !== "live" && <FilterRow />}
        <main>
          {view === "exposure" && <ExposureView />}
          {view === "risk" && <RiskView />}
          {view === "scenario" && <ScenarioView />}
          {view === "live" && <LiveView />}
          {view === "dashboards" && <DashboardsView />}
          {view === "analyses" && <AnalysesView />}
        </main>
      </div>
      <CommandPalette />
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className="toast" onClick={() => dismiss(t.id)}>
            <Status tone={t.tone}>{t.title}</Status>
            {t.body && <div className="small muted">{t.body}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
