/**
 * Single application state. One filter object scopes every widget, map layer and server query,
 * and the whole navigational state round-trips through the URL hash, so any view is a
 * shareable deep link (e.g. #/risk?p=…&r=…&hub=Miami&e=2000123).
 */
import { create } from "zustand";
import { EMPTY_FILTERS, type Dim, type Filters } from "../lib/columns";
import type { Alert, Perspective } from "../api/client";

export type View = "exposure" | "risk" | "scenario" | "live" | "dashboards" | "analyses";
export const VIEWS: { id: View; label: string; key: string }[] = [
  { id: "exposure", label: "Exposure", key: "1" },
  { id: "risk", label: "Risk explorer", key: "2" },
  { id: "scenario", label: "Scenario lab", key: "3" },
  { id: "live", label: "Live", key: "4" },
  { id: "dashboards", label: "Dashboards", key: "5" },
  { id: "analyses", label: "Analyses", key: "6" },
];

export type Theme = "system" | "light" | "dark";

export interface Toast {
  id: number;
  tone: "info" | "good" | "warning" | "critical";
  title: string;
  body?: string;
}

interface State {
  view: View;
  portfolioId: string | null;
  runId: string | null;
  eventId: number | null;
  perspective: Perspective;
  filters: Filters;
  theme: Theme;
  paletteOpen: boolean;
  ws: "connecting" | "open" | "closed";
  progress: Record<string, { progress: number; message: string }>;
  versions: { portfolios: number; runs: number; live: number; alerts: number };
  toasts: Toast[];
  alerts: Alert[];
  setView: (v: View) => void;
  setPortfolio: (id: string | null) => void;
  setRun: (id: string | null) => void;
  setEvent: (id: number | null) => void;
  setPerspective: (p: Perspective) => void;
  toggle: (dim: Dim, value: string | number) => void;
  setFilter: (dim: Dim, values: (string | number)[]) => void;
  clearFilters: () => void;
  setTheme: (t: Theme) => void;
  setPalette: (open: boolean) => void;
  bump: (k: keyof State["versions"]) => void;
  toast: (t: Omit<Toast, "id">) => void;
  dismiss: (id: number) => void;
}

const DIM_KEY: Record<Dim, keyof Filters> = {
  hub: "hubs",
  occupancy: "occupancies",
  construction: "constructions",
  peril: "perils",
  h3: "h3",
};

function readHash(): Partial<State> {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [path, query = ""] = raw.split("?");
  const q = new URLSearchParams(query);
  const view = (VIEWS.find((v) => v.id === path)?.id ?? "exposure") as View;
  const nums = (k: string) => q.getAll(k).map(Number).filter((n) => Number.isFinite(n));
  const p = q.get("persp");
  return {
    view,
    portfolioId: q.get("p"),
    runId: q.get("r"),
    eventId: q.get("e") ? Number(q.get("e")) : null,
    perspective: p === "gu" || p === "net" ? p : "gross",
    filters: {
      perils: q.getAll("peril"),
      hubs: q.getAll("hub"),
      occupancies: nums("occ"),
      constructions: nums("cons"),
      h3: q.getAll("h3"),
    },
  };
}

function writeHash(s: State): void {
  const q = new URLSearchParams();
  if (s.portfolioId) q.set("p", s.portfolioId);
  if (s.runId) q.set("r", s.runId);
  if (s.eventId != null) q.set("e", String(s.eventId));
  if (s.perspective !== "gross") q.set("persp", s.perspective);
  s.filters.perils.forEach((v) => q.append("peril", v));
  s.filters.hubs.forEach((v) => q.append("hub", v));
  s.filters.occupancies.forEach((v) => q.append("occ", String(v)));
  s.filters.constructions.forEach((v) => q.append("cons", String(v)));
  s.filters.h3.forEach((v) => q.append("h3", v));
  const next = `#/${s.view}${q.toString() ? `?${q}` : ""}`;
  if (window.location.hash !== next) window.history.replaceState(null, "", next);
}

function initialTheme(): Theme {
  try {
    const t = localStorage.getItem("catmod.theme");
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

let toastId = 0;

export const useStore = create<State>((set) => ({
  view: "exposure",
  portfolioId: null,
  runId: null,
  eventId: null,
  perspective: "gross",
  filters: EMPTY_FILTERS,
  theme: initialTheme(),
  paletteOpen: false,
  ws: "connecting",
  progress: {},
  versions: { portfolios: 0, runs: 0, live: 0, alerts: 0 },
  toasts: [],
  alerts: [],
  ...readHash(),
  setView: (view) => set({ view }),
  setPortfolio: (portfolioId) =>
    set((s) => (s.portfolioId === portfolioId ? {} : { portfolioId, runId: null, eventId: null, filters: EMPTY_FILTERS })),
  setRun: (runId) => set((s) => (s.runId === runId ? {} : { runId, eventId: null })),
  setEvent: (eventId) => set({ eventId }),
  setPerspective: (perspective) => set({ perspective }),
  toggle: (dim, value) =>
    set((s) => {
      const k = DIM_KEY[dim];
      const cur = s.filters[k] as (string | number)[];
      const next = cur.includes(value) ? cur.filter((v) => v !== value) : [...cur, value];
      return { filters: { ...s.filters, [k]: next } };
    }),
  setFilter: (dim, values) => set((s) => ({ filters: { ...s.filters, [DIM_KEY[dim]]: values } })),
  clearFilters: () => set({ filters: EMPTY_FILTERS }),
  setTheme: (theme) => {
    try {
      localStorage.setItem("catmod.theme", theme);
    } catch {
      /* storage unavailable */
    }
    set({ theme });
  },
  setPalette: (paletteOpen) => set({ paletteOpen }),
  bump: (k) => set((s) => ({ versions: { ...s.versions, [k]: s.versions[k] + 1 } })),
  toast: (t) =>
    set((s) => {
      const id = ++toastId;
      setTimeout(() => useStore.getState().dismiss(id), 6000);
      return { toasts: [...s.toasts.slice(-3), { ...t, id }] };
    }),
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

useStore.subscribe((s) => writeHash(s));
window.addEventListener("hashchange", () => {
  const h = readHash();
  const s = useStore.getState();
  if (h.view !== s.view || h.portfolioId !== s.portfolioId || h.runId !== s.runId || h.eventId !== s.eventId) {
    useStore.setState(h);
  }
});

/** Resolved light/dark mode for charts and maps. */
export function useMode(): "light" | "dark" {
  const theme = useStore((s) => s.theme);
  if (theme !== "system") return theme;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
