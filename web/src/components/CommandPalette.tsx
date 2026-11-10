import { useEffect, useMemo, useRef, useState } from "react";
import { api, unwrap } from "../api/client";
import { usePortfolios, useRuns } from "../lib/hooks";
import { useStore, VIEWS } from "../state/store";

interface Cmd {
  id: string;
  label: string;
  hint?: string;
  run: () => void | Promise<void>;
}

/** ⌘K / Ctrl-K: fuzzy command palette over views, portfolios, runs, events and actions. */
export function CommandPalette() {
  const open = useStore((s) => s.paletteOpen);
  const pid = useStore((s) => s.portfolioId);
  const s = useStore.getState();
  const portfolios = usePortfolios();
  const runs = useRuns(pid);
  const [q, setQ] = useState("");
  const [i, setI] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQ("");
      setI(0);
      setTimeout(() => input.current?.focus(), 0);
    }
  }, [open]);

  const cmds = useMemo<Cmd[]>(() => {
    const out: Cmd[] = [
      ...VIEWS.map((v) => ({ id: `view:${v.id}`, label: `Go to ${v.label}`, hint: v.key, run: () => s.setView(v.id) })),
      ...(portfolios.data ?? []).map((p) => ({ id: `p:${p.id}`, label: `Portfolio: ${p.name}`, hint: `${p.summary.n_locations.toLocaleString()} locs`, run: () => s.setPortfolio(p.id) })),
      ...(runs.data ?? []).map((r) => ({ id: `r:${r.id}`, label: `Run: ${r.name}`, hint: r.status, run: () => { s.setRun(r.id); s.setView("risk"); } })),
      { id: "clear", label: "Clear all filters", hint: "Esc", run: () => s.clearFilters() },
      { id: "persp:gu", label: "Perspective: ground-up", run: () => s.setPerspective("gu") },
      { id: "persp:gross", label: "Perspective: gross", run: () => s.setPerspective("gross") },
      { id: "persp:net", label: "Perspective: net of reinsurance", run: () => s.setPerspective("net") },
      { id: "theme:light", label: "Theme: light", run: () => s.setTheme("light") },
      { id: "theme:dark", label: "Theme: dark", run: () => s.setTheme("dark") },
      { id: "theme:system", label: "Theme: follow system", run: () => s.setTheme("system") },
      { id: "live:refresh", label: "Live: refresh feeds now", run: async () => { await unwrap(api.POST("/api/v1/live/refresh")); s.bump("live"); s.setView("live"); } },
      { id: "copy", label: "Copy link to this view", run: () => navigator.clipboard?.writeText(window.location.href) },
      { id: "apikey", label: "Set API key…", run: () => {
        const k = window.prompt("API key (stored in this browser only)");
        try { if (k !== null) localStorage.setItem("catmod.apiKey", k); } catch { /* ignore */ }
      } },
    ];
    const n = Number(q.trim().replace(/^#/, ""));
    if (Number.isInteger(n) && n > 1000) {
      out.unshift({ id: `e:${n}`, label: `Open event #${n}`, hint: "in current run", run: () => { s.setEvent(n); s.setView("risk"); } });
    }
    return out;
  }, [portfolios.data, runs.data, q, s]);

  const filtered = useMemo(() => {
    const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
    return cmds.filter((c) => terms.every((t) => c.label.toLowerCase().includes(t) || c.id.includes(t))).slice(0, 12);
  }, [cmds, q]);

  if (!open) return null;
  const exec = async (c: Cmd | undefined) => {
    if (!c) return;
    s.setPalette(false);
    await c.run();
  };
  return (
    <div className="palette-backdrop" onClick={() => s.setPalette(false)}>
      <div className="palette" role="dialog" aria-label="Command palette" onClick={(e) => e.stopPropagation()}>
        <input
          ref={input}
          value={q}
          placeholder="Type a command, portfolio, run, or event id…"
          onChange={(e) => { setQ(e.target.value); setI(0); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setI((x) => Math.min(filtered.length - 1, x + 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setI((x) => Math.max(0, x - 1)); }
            if (e.key === "Enter") exec(filtered[i]);
            if (e.key === "Escape") s.setPalette(false);
          }}
          aria-controls="palette-list"
        />
        <ul id="palette-list" role="listbox">
          {filtered.map((c, k) => (
            <li key={c.id} role="option" aria-selected={k === i} className={k === i ? "active" : ""}
                onMouseEnter={() => setI(k)} onClick={() => exec(c)}>
              <span>{c.label}</span>{c.hint && <kbd>{c.hint}</kbd>}
            </li>
          ))}
          {!filtered.length && <li className="muted">No matches</li>}
        </ul>
      </div>
    </div>
  );
}
