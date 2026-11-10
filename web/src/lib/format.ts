const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const compact2 = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 });
const integer = new Intl.NumberFormat("en-US");

export const fmtMoney = (v: number | null | undefined, ccy = "USD") =>
  v == null || !Number.isFinite(v) ? "–" : `${compact.format(v)} ${ccy}`;
export const fmtNum = (v: number | null | undefined) => (v == null ? "–" : compact2.format(v));
export const fmtInt = (v: number | null | undefined) => (v == null ? "–" : integer.format(v));
export const fmtPct = (v: number | null | undefined, digits = 1) =>
  v == null || !Number.isFinite(v) ? "–" : `${(v * 100).toFixed(digits)}%`;
export const fmtRp = (v: number) => (v >= 100 ? integer.format(Math.round(v)) : v.toFixed(v < 10 ? 1 : 0));
export const fmtRate = (r: number) => (r > 0 ? `1 in ${integer.format(Math.round(1 / r))} yr` : "–");
export const fmtTime = (iso: string) => {
  const d = new Date(iso);
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 48 * 60) return `${Math.round(mins / 60)} h ago`;
  return d.toLocaleDateString();
};
