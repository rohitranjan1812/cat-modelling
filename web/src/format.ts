const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const integer = new Intl.NumberFormat("en-US");

export const fmtMoney = (v: number, ccy = "USD") => `${compact.format(v)} ${ccy}`;
export const fmtInt = (v: number) => integer.format(v);
export const fmtPct = (v: number) => `${(v * 100).toFixed(1)}%`;
