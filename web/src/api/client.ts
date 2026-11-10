import createClient, { type Middleware } from "openapi-fetch";
import { tableFromIPC, type Table } from "apache-arrow";
import type { components, paths } from "./schema";

/** Typed client generated from schemas/openapi.json — the backend is the source of truth. */
export const BASE = import.meta.env.VITE_API_URL ?? "";

export function apiKey(): string | null {
  try {
    return localStorage.getItem("catmod.apiKey");
  } catch {
    return null;
  }
}

const auth: Middleware = {
  onRequest({ request }) {
    const k = apiKey();
    if (k) request.headers.set("x-api-key", k);
    return request;
  },
};

export const api = createClient<paths>({ baseUrl: BASE });
api.use(auth);

type S = components["schemas"];
// Response-derived types match exactly what openapi-fetch yields.
const _lp = () => api.GET("/api/v1/portfolios");
export type PortfolioMeta = NonNullable<Awaited<ReturnType<typeof _lp>>["data"]>[number];
const _lr = () => api.GET("/api/v1/runs");
export type RunMeta = NonNullable<Awaited<ReturnType<typeof _lr>>["data"]>[number];
export type EPResponse = S["EPResponse"];
export type BreakdownRow = S["BreakdownRow"];
export type EventRow = S["EventRow"];
export type EventDrill = S["EventDrill"];
export type ScenarioResult = S["ScenarioResult"];
export type CodeLists = S["CodeLists"];
export type Capabilities = S["Capabilities"];
export type LiveEvent = S["LiveEventState"];
export type Nowcast = S["Nowcast"];
export type Alert = S["Alert"];
export type AlertRule = S["AlertRule"];
export type Dashboard = S["Dashboard"];
export type DashboardIn = S["DashboardIn"];
export type Widget = S["Widget"];
export type AnalysisConfig = S["AnalysisConfig"];
export type PerspectiveMetrics = S["PerspectiveMetrics"];
export type Perspective = "gu" | "gross" | "net";

/** Unwrap an openapi-fetch result or throw a readable error. */
export async function unwrap<T>(p: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await p;
  if (data !== undefined && response.ok) return data;
  const detail = (error as { detail?: unknown } | undefined)?.detail ?? error ?? response.statusText;
  throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
}

export async function fetchArrow(path: string): Promise<Table> {
  const headers: Record<string, string> = {};
  const k = apiKey();
  if (k) headers["x-api-key"] = k;
  const r = await fetch(`${BASE}${path}`, { headers });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return tableFromIPC(new Uint8Array(await r.arrayBuffer()));
}
