"""Risk metrics from a year loss table.

Exceedance curves are empirical: with N simulated years, the T-year return-period loss is the
ceil(N/T)-th largest annual value (annual maximum for OEP, annual sum for AEP).
Standard errors use non-overlapping batch means: years are split into B equal batches, the metric
is computed per batch, and SE = sd(batch values) / sqrt(B). Metrics whose return period exceeds
the batch length get no SE (reported as null) instead of a misleading number.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel

F64 = npt.NDArray[np.float64]
I64 = npt.NDArray[np.int64]

DEFAULT_RPS = [2, 5, 10, 25, 50, 100, 200, 250, 500, 1000]
DEFAULT_LEVELS = [0.99, 0.995, 0.996]


def annual(year: I64, loss: F64, n_years: int) -> tuple[F64, F64]:
    """(aggregate, occurrence max) per year, including zero-loss years."""
    agg = np.bincount(year, weights=loss, minlength=n_years)
    occ = np.zeros(n_years)
    np.maximum.at(occ, year, loss)
    return agg, occ


def rp_value(values: F64, rp: float) -> float:
    n = values.size
    k = int(np.ceil(n / rp - 1e-9)) - 1
    if k >= n or k < 0:
        return 0.0
    return float(np.partition(values, n - 1 - k)[n - 1 - k])


def tvar(values: F64, level: float) -> tuple[float, float]:
    """(VaR, TVaR) at confidence level, from annual values."""
    n = values.size
    k = max(1, int(np.ceil(n * (1.0 - level) - 1e-9)))
    top = np.partition(values, n - k)[n - k :]
    return float(np.min(top)), float(np.mean(top))


class PerspectiveMetrics(BaseModel):
    aal: float
    aal_se: float | None
    sd: float
    oep: dict[str, float]
    oep_se: dict[str, float | None]
    aep: dict[str, float]
    aep_se: dict[str, float | None]
    var: dict[str, float]
    tvar: dict[str, float]
    tvar_se: dict[str, float | None]


class CurvePoint(BaseModel):
    rp: float
    oep: float
    aep: float


def _batch_se(values: F64, n_batches: int, fn: object, horizon: float) -> float | None:
    n = values.size
    size = n // n_batches
    if n_batches < 2 or size < max(2.0, horizon):
        return None
    batches = values[: size * n_batches].reshape(n_batches, size)
    vals = np.array([fn(b) for b in batches])  # type: ignore[operator]
    return float(vals.std(ddof=1) / np.sqrt(n_batches))


def perspective_metrics(
    year: I64,
    loss: F64,
    n_years: int,
    rps: list[int] | None = None,
    levels: list[float] | None = None,
    n_batches: int = 20,
) -> PerspectiveMetrics:
    rps = rps or DEFAULT_RPS
    levels = levels or DEFAULT_LEVELS
    agg, occ = annual(year, loss, n_years)
    return PerspectiveMetrics(
        aal=float(agg.mean()),
        aal_se=_batch_se(agg, n_batches, np.mean, 1.0),
        sd=float(agg.std(ddof=1)) if n_years > 1 else 0.0,
        oep={str(r): rp_value(occ, r) for r in rps},
        oep_se={str(r): _batch_se(occ, n_batches, lambda v, r=r: rp_value(v, r), r) for r in rps},
        aep={str(r): rp_value(agg, r) for r in rps},
        aep_se={str(r): _batch_se(agg, n_batches, lambda v, r=r: rp_value(v, r), r) for r in rps},
        var={str(lv): tvar(agg, lv)[0] for lv in levels},
        tvar={str(lv): tvar(agg, lv)[1] for lv in levels},
        tvar_se={
            str(lv): _batch_se(agg, n_batches, lambda v, lv=lv: tvar(v, lv)[1], 1 / (1 - lv))
            for lv in levels
        },
    )


def curve(year: I64, loss: F64, n_years: int, n_points: int = 60) -> list[CurvePoint]:
    agg, occ = annual(year, loss, n_years)
    agg_s, occ_s = np.sort(agg)[::-1], np.sort(occ)[::-1]
    rps = np.unique(np.geomspace(1.0, max(n_years, 2), n_points).round(2))
    out = []
    for rp in rps:
        k = int(np.ceil(n_years / rp - 1e-9)) - 1
        if 0 <= k < n_years:
            out.append(CurvePoint(rp=float(rp), oep=float(occ_s[k]), aep=float(agg_s[k])))
    return out
