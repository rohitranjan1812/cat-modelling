/**
 * Validated palette (dataviz reference instance). Colour follows the entity, never its rank:
 * perils own fixed categorical slots; magnitudes use one-hue sequential ramps.
 * Validation: perils pass all categorical checks in light and dark (CVD ΔE 24.7 / 26.8);
 * ramps are monotone single-hue.
 */
export type Mode = "light" | "dark";

export const PERIL_COLOR: Record<string, Record<Mode, string>> = {
  QEQ: { light: "#2a78d6", dark: "#3987e5" }, // slot 1 blue
  WTC: { light: "#eb6834", dark: "#d95926" }, // slot 2 orange
};

export const PERIL_LABEL: Record<string, string> = { QEQ: "Earthquake", WTC: "Tropical cyclone" };

/** Blue sequential ramp 100→700 (magnitude: TIV, AAL, loss). */
export const BLUE_RAMP = [
  "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
  "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
];

/** Orange sequential ramp (second sequential context: hazard intensity). */
export const ORANGE_RAMP = ["#fde0d2", "#f7b393", "#ef8a5c", "#eb6834", "#c9531f", "#9c3e15", "#6e2b0c"];

export const STATUS = { good: "#0ca30c", warning: "#fab219", serious: "#ec835a", critical: "#d03b3b" };

export function perilColor(p: string, mode: Mode): string {
  return PERIL_COLOR[p]?.[mode] ?? (mode === "dark" ? "#c3c2b7" : "#52514e");
}

function hexToRgb(h: string): [number, number, number] {
  const v = parseInt(h.slice(1), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

/** Interpolate a ramp at t∈[0,1] → RGB triple (for deck.gl). */
export function ramp(stops: string[], t: number): [number, number, number] {
  const x = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  const f = x - i;
  const a = hexToRgb(stops[i]!);
  const b = hexToRgb(stops[i + 1]!);
  return [0, 1, 2].map((k) => Math.round(a[k]! + (b[k]! - a[k]!) * f)) as [number, number, number];
}

export function rgb(hex: string): [number, number, number] {
  return hexToRgb(hex);
}

/** Robust scaler: sqrt over [0, p98] so one hotspot does not wash out the map. */
export function scaler(values: ArrayLike<number>): (v: number) => number {
  const arr = Array.from(values).filter((v) => v > 0).sort((a, b) => a - b);
  const hi = arr.length ? arr[Math.floor(arr.length * 0.98)] ?? arr[arr.length - 1]! : 1;
  const top = Math.sqrt(hi || 1);
  return (v: number) => Math.min(1, Math.sqrt(Math.max(0, v)) / top);
}
