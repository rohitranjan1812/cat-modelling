import { useEffect, useRef, useState } from "react";
import { api, fetchArrow, unwrap, type CodeLists, type PortfolioMeta, type RunMeta } from "../api/client";
import { useStore } from "../state/store";
import { toLocTable, type LocTable } from "./columns";

export interface Async<T> {
  data: T | undefined;
  error: string | null;
  loading: boolean;
}

/** Async loader that keeps the previous frame while refetching (no skeleton flash). */
export function useAsync<T>(fn: () => Promise<T> | null, deps: unknown[]): Async<T> {
  const [state, setState] = useState<Async<T>>({ data: undefined, error: null, loading: false });
  const seq = useRef(0);
  useEffect(() => {
    const p = fn();
    if (!p) {
      setState({ data: undefined, error: null, loading: false });
      return;
    }
    const id = ++seq.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    p.then(
      (data) => id === seq.current && setState({ data, error: null, loading: false }),
      (e: unknown) => id === seq.current && setState((s) => ({ ...s, error: String((e as Error).message ?? e), loading: false })),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function usePortfolios(): Async<PortfolioMeta[]> {
  const v = useStore((s) => s.versions.portfolios);
  return useAsync(() => unwrap(api.GET("/api/v1/portfolios")), [v]);
}

export function useRuns(portfolioId: string | null): Async<RunMeta[]> {
  const v = useStore((s) => s.versions.runs);
  return useAsync(
    () => (portfolioId ? unwrap(api.GET("/api/v1/runs", { params: { query: { portfolio_id: portfolioId } } })) : null),
    [portfolioId, v],
  );
}

let codesCache: Promise<CodeLists> | null = null;
export function useCodes(): CodeLists | undefined {
  const r = useAsync(() => (codesCache ??= unwrap(api.GET("/api/v1/codes"))), []);
  return r.data;
}

const tables = new Map<string, Promise<LocTable>>();
/** Columnar location table for a portfolio (exposure) or a run (exposure + AAL), memoised. */
export function useLocTable(kind: "portfolio" | "run", id: string | null): Async<LocTable> {
  return useAsync(() => {
    if (!id) return null;
    const key = `${kind}:${id}`;
    if (!tables.has(key)) {
      const path = kind === "run" ? `/api/v1/runs/${id}/locations.arrow` : `/api/v1/portfolios/${id}/locations.arrow`;
      tables.set(key, fetchArrow(path).then(toLocTable).catch((e) => {
        tables.delete(key);
        throw e;
      }));
    }
    return tables.get(key)!;
  }, [kind, id]);
}

export function codeLabel(codes: CodeLists | undefined, dim: string, key: string): string {
  if (!codes) return key;
  const m: Record<string, Record<string, string>> = {
    occupancy: codes.occupancy as Record<string, string>,
    construction: codes.construction as Record<string, string>,
    peril: codes.perils,
  };
  const name = m[dim]?.[key];
  return name ? name : key;
}
