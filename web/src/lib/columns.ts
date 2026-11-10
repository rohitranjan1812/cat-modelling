/**
 * Columnar location table + a crossfilter over typed arrays.
 *
 * Instead of DuckDB-WASM (~30 MB) we keep each column as a typed array (dictionary-encoded for
 * strings) and evaluate filters as a Uint8Array mask. Group-bys are a single pass with
 * Float64Array accumulators: < 10 ms for 1M rows. Classic crossfilter semantics: a widget's
 * own dimension filter is excluded when computing that widget, so you can still pick siblings.
 */
import type { Table } from "apache-arrow";

export interface Dict {
  codes: Int32Array;
  labels: string[];
}

export interface LocTable {
  n: number;
  lat: Float64Array;
  lon: Float64Array;
  tiv: Float64Array;
  hub: Dict;
  occ: Int32Array;
  cons: Int32Array;
  year: Int32Array;
  perils: Dict; // LocPerilsCovered string, e.g. "QEQ" or "QEQ;WTC"
  locNumber: (i: number) => string;
  accNumber: (i: number) => string;
  h3: Record<string, string[]>; // resolution -> hex per row (lazy)
  h3Raw: Record<string, BigUint64Array | null>;
  aal: Record<string, Float64Array>; // e.g. aal_gross, aal_gu_QEQ
}

function dict(values: (string | null)[]): Dict {
  const idx = new Map<string, number>();
  const labels: string[] = [];
  const codes = new Int32Array(values.length);
  values.forEach((v, i) => {
    const s = v ?? "";
    let c = idx.get(s);
    if (c === undefined) {
      c = labels.length;
      labels.push(s);
      idx.set(s, c);
    }
    codes[i] = c;
  });
  return { codes, labels };
}

function f64(t: Table, name: string): Float64Array {
  const col = t.getChild(name);
  if (!col) return new Float64Array(t.numRows);
  const out = new Float64Array(t.numRows);
  const arr = col.toArray() as ArrayLike<number | bigint>;
  for (let i = 0; i < out.length; i++) out[i] = Number(arr[i] ?? 0);
  return out;
}

function i32(t: Table, name: string): Int32Array {
  const out = new Int32Array(t.numRows);
  const col = t.getChild(name);
  if (!col) return out;
  const arr = col.toArray() as ArrayLike<number | bigint>;
  for (let i = 0; i < out.length; i++) out[i] = Number(arr[i] ?? 0);
  return out;
}

export function toLocTable(t: Table): LocTable {
  const strings = (name: string) => (t.getChild(name)?.toArray() as (string | null)[] | undefined) ?? [];
  const loc = t.getChild("LocNumber");
  const acc = t.getChild("AccNumber");
  const h3Raw: Record<string, BigUint64Array | null> = {};
  for (const f of t.schema.fields) {
    const m = /^catmod_h3_r(\d+)$/.exec(f.name);
    if (m) h3Raw[m[1]!] = t.getChild(f.name)!.toArray() as BigUint64Array;
  }
  const aal: Record<string, Float64Array> = {};
  for (const f of t.schema.fields) if (f.name.startsWith("aal_")) aal[f.name] = f64(t, f.name);
  const h3Cache: Record<string, string[]> = {};
  const table: LocTable = {
    n: t.numRows,
    lat: f64(t, "Latitude"),
    lon: f64(t, "Longitude"),
    tiv: f64(t, "catmod_tiv"),
    hub: dict(strings("FlexiLocHub")),
    occ: i32(t, "OccupancyCode"),
    cons: i32(t, "ConstructionCode"),
    year: i32(t, "YearBuilt"),
    perils: dict(strings("LocPerilsCovered")),
    locNumber: (i) => String(loc?.get(i) ?? ""),
    accNumber: (i) => String(acc?.get(i) ?? ""),
    h3Raw,
    aal,
    h3: new Proxy(h3Cache, {
      get(target, res: string) {
        if (!(res in target)) {
          const raw = h3Raw[res];
          target[res] = raw ? Array.from(raw, (v) => v.toString(16)) : [];
        }
        return target[res];
      },
    }),
  };
  return table;
}

export interface Filters {
  perils: string[];
  hubs: string[];
  occupancies: number[];
  constructions: number[];
  h3: string[];
}

export type Dim = "hub" | "occupancy" | "construction" | "peril" | "h3";

export const EMPTY_FILTERS: Filters = { perils: [], hubs: [], occupancies: [], constructions: [], h3: [] };

export function activeCount(f: Filters): number {
  return f.perils.length + f.hubs.length + f.occupancies.length + f.constructions.length + f.h3.length;
}

/** Row mask under all filters except `except` (crossfilter semantics). */
export function mask(t: LocTable, f: Filters, except?: Dim, h3Res = "5"): Uint8Array {
  const m = new Uint8Array(t.n).fill(1);
  if (f.hubs.length && except !== "hub") {
    const ok = new Set(f.hubs.map((h) => t.hub.labels.indexOf(h)));
    for (let i = 0; i < t.n; i++) if (!ok.has(t.hub.codes[i]!)) m[i] = 0;
  }
  if (f.occupancies.length && except !== "occupancy") {
    const ok = new Set(f.occupancies);
    for (let i = 0; i < t.n; i++) if (!ok.has(t.occ[i]!)) m[i] = 0;
  }
  if (f.constructions.length && except !== "construction") {
    const ok = new Set(f.constructions);
    for (let i = 0; i < t.n; i++) if (!ok.has(t.cons[i]!)) m[i] = 0;
  }
  if (f.perils.length && except !== "peril") {
    const ok = t.perils.labels.map((l) => l.split(";").some((p) => f.perils.includes(p)));
    for (let i = 0; i < t.n; i++) if (!ok[t.perils.codes[i]!]) m[i] = 0;
  }
  if (f.h3.length && except !== "h3") {
    const ok = new Set(f.h3);
    const cells = t.h3[h3Res] ?? [];
    for (let i = 0; i < t.n; i++) if (!ok.has(cells[i]!)) m[i] = 0;
  }
  return m;
}

/** AAL vector respecting the peril filter (sum of per-peril columns), for a perspective. */
export function aalVector(t: LocTable, perspective: "gu" | "gross", perils: string[]): Float64Array | null {
  const all = Object.keys(t.aal)
    .filter((k) => k.startsWith(`aal_${perspective}_`))
    .map((k) => k.slice(`aal_${perspective}_`.length));
  if (!all.length) return t.aal[`aal_${perspective}`] ?? null;
  const use = perils.length ? all.filter((p) => perils.includes(p)) : all;
  const out = new Float64Array(t.n);
  for (const p of use) {
    const v = t.aal[`aal_${perspective}_${p}`]!;
    for (let i = 0; i < t.n; i++) out[i]! += v[i]!;
  }
  return out;
}

export interface Group {
  key: string;
  n: number;
  tiv: number;
  value: number; // metric (AAL or TIV)
  lat: number;
  lon: number;
}

export function groupBy(
  t: LocTable,
  keyOf: (i: number) => string,
  m: Uint8Array,
  metric: Float64Array | null,
): Group[] {
  const acc = new Map<string, Group>();
  for (let i = 0; i < t.n; i++) {
    if (!m[i]) continue;
    const k = keyOf(i);
    let g = acc.get(k);
    if (!g) {
      g = { key: k, n: 0, tiv: 0, value: 0, lat: 0, lon: 0 };
      acc.set(k, g);
    }
    g.n++;
    g.tiv += t.tiv[i]!;
    g.value += metric ? metric[i]! : t.tiv[i]!;
    g.lat += t.lat[i]!;
    g.lon += t.lon[i]!;
  }
  const out = [...acc.values()];
  for (const g of out) {
    g.lat /= g.n;
    g.lon /= g.n;
  }
  return out.sort((a, b) => b.value - a.value);
}

export function keyFn(t: LocTable, dim: Dim, h3Res = "5"): (i: number) => string {
  switch (dim) {
    case "hub":
      return (i) => t.hub.labels[t.hub.codes[i]!]!;
    case "occupancy":
      return (i) => String(t.occ[i]);
    case "construction":
      return (i) => String(t.cons[i]);
    case "peril":
      return (i) => t.perils.labels[t.perils.codes[i]!]!;
    case "h3": {
      const cells = t.h3[h3Res] ?? [];
      return (i) => cells[i] ?? "";
    }
  }
}
