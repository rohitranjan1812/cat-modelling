import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type AggregateRow, type CodeLists, type Dimension, type PortfolioMeta } from "./api/client";
import { fmtInt, fmtMoney, fmtPct } from "./format";

const DIMENSIONS: Dimension[] = ["hub", "peril", "occupancy", "construction", "country", "h3"];

function useHealth() {
  const [version, setVersion] = useState<string | null>(null);
  const [ok, setOk] = useState<boolean | null>(null);
  useEffect(() => {
    api.GET("/health").then(
      ({ data }) => {
        setOk(Boolean(data));
        setVersion(data?.version ?? null);
      },
      () => setOk(false),
    );
  }, []);
  return { ok, version };
}

function GenerateForm({ onCreated }: { onCreated: (m: PortfolioMeta) => void }) {
  const [presets, setPresets] = useState<string[]>([]);
  const [preset, setPreset] = useState("");
  const [n, setN] = useState(50_000);
  const [seed, setSeed] = useState(42);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.GET("/api/v1/presets").then(({ data }) => {
      setPresets(data ?? []);
      setPreset((p) => p || data?.[0] || "");
    });
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const { data, error: err } = await api.POST("/api/v1/portfolios", {
      body: { preset, n_locations: n, seed },
    });
    setBusy(false);
    if (data) onCreated(data);
    else setError(JSON.stringify(err?.detail ?? err));
  };

  return (
    <form className="panel form" onSubmit={submit}>
      <h2>Generate portfolio</h2>
      <label>
        Preset
        <select value={preset} onChange={(e) => setPreset(e.target.value)}>
          {presets.map((p) => (
            <option key={p}>{p}</option>
          ))}
        </select>
      </label>
      <label>
        Locations
        <input type="number" min={1} max={5_000_000} value={n} onChange={(e) => setN(+e.target.value)} />
      </label>
      <label>
        Seed
        <input type="number" min={0} value={seed} onChange={(e) => setSeed(+e.target.value)} />
      </label>
      <button disabled={busy || !preset}>{busy ? "Generating…" : "Generate"}</button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

function Kpis({ meta }: { meta: PortfolioMeta }) {
  const s = meta.summary;
  const ccy = meta.config.currency ?? "USD";
  const items: [string, string][] = [
    ["Locations", fmtInt(s.n_locations)],
    ["Accounts", fmtInt(s.n_accounts)],
    ["Total TIV", fmtMoney(s.tiv_total, ccy)],
    ["Building", fmtPct(s.tiv_building / s.tiv_total)],
    ["Contents", fmtPct(s.tiv_contents / s.tiv_total)],
    ["BI", fmtPct(s.tiv_bi / s.tiv_total)],
  ];
  return (
    <div className="kpis">
      {items.map(([k, v]) => (
        <div key={k} className="kpi">
          <span className="kpi-label">{k}</span>
          <span className="kpi-value">{v}</span>
        </div>
      ))}
    </div>
  );
}

function label(dim: Dimension, key: string, codes: CodeLists | null): string {
  if (!codes) return key;
  const lookup: Partial<Record<Dimension, Record<string, string>>> = {
    occupancy: codes.occupancy,
    construction: codes.construction,
    peril: codes.perils,
  };
  const name = lookup[dim]?.[key];
  return name ? `${key} · ${name}` : key;
}

function Breakdown({ meta, codes }: { meta: PortfolioMeta; codes: CodeLists | null }) {
  const [dim, setDim] = useState<Dimension>("hub");
  const [rows, setRows] = useState<AggregateRow[]>([]);

  useEffect(() => {
    api
      .GET("/api/v1/portfolios/{pid}/aggregate", { params: { path: { pid: meta.id }, query: { by: dim } } })
      .then(({ data }) => setRows(data ?? []));
  }, [meta.id, dim]);

  const total = useMemo(() => rows.reduce((a, r) => a + r.tiv, 0), [rows]);
  const shown = rows.slice(0, 25);

  return (
    <section className="panel">
      <div className="tabs" role="tablist">
        {DIMENSIONS.map((d) => (
          <button key={d} role="tab" aria-selected={d === dim} className={d === dim ? "tab active" : "tab"} onClick={() => setDim(d)}>
            {d}
          </button>
        ))}
      </div>
      <table>
        <thead>
          <tr>
            <th>{dim}</th>
            <th className="num">Locations</th>
            <th className="num">TIV</th>
            <th className="bar-col">Share</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((r) => (
            <tr key={r.key}>
              <td>{label(dim, r.key, codes)}</td>
              <td className="num">{fmtInt(r.n_locations)}</td>
              <td className="num">{fmtMoney(r.tiv, meta.config.currency ?? "USD")}</td>
              <td className="bar-col">
                <div className="bar" style={{ width: `${(100 * r.tiv) / (total || 1)}%` }} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > shown.length && <p className="muted">Showing top {shown.length} of {fmtInt(rows.length)}</p>}
    </section>
  );
}

export function App() {
  const health = useHealth();
  const [codes, setCodes] = useState<CodeLists | null>(null);
  const [portfolios, setPortfolios] = useState<PortfolioMeta[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const { data } = await api.GET("/api/v1/portfolios");
    setPortfolios(data ?? []);
    setSelected((s) => s ?? data?.[0]?.id ?? null);
  }, []);

  useEffect(() => {
    refresh();
    api.GET("/api/v1/codes").then(({ data }) => setCodes(data ?? null));
  }, [refresh]);

  const current = portfolios.find((p) => p.id === selected) ?? null;

  return (
    <div className="app">
      <header className="topbar">
        <strong>catmod</strong>
        <span className="muted">open catastrophe modelling</span>
        <span className={`status ${health.ok ? "up" : health.ok === false ? "down" : ""}`}>
          API {health.ok === null ? "…" : health.ok ? `v${health.version}` : "unreachable"}
        </span>
      </header>
      <main className="layout">
        <aside>
          <GenerateForm
            onCreated={(m) => {
              setSelected(m.id);
              refresh();
            }}
          />
          <nav className="panel list">
            <h2>Portfolios</h2>
            {portfolios.length === 0 && <p className="muted">None yet. Generate one above.</p>}
            {portfolios.map((p) => (
              <button key={p.id} className={p.id === selected ? "item active" : "item"} onClick={() => setSelected(p.id)}>
                <span>{p.name}</span>
                <span className="muted">
                  {fmtInt(p.summary.n_locations)} locs · seed {p.config.seed} · {p.generation_ms} ms
                </span>
              </button>
            ))}
          </nav>
        </aside>
        <div className="content">
          {current ? (
            <>
              <h1>{current.name}</h1>
              <Kpis meta={current} />
              <Breakdown meta={current} codes={codes} />
            </>
          ) : (
            <p className="muted">Select or generate a portfolio.</p>
          )}
        </div>
      </main>
    </div>
  );
}
