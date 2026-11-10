"""Financial module: location terms -> account/policy layer -> reinsurance.

Order of operations (per event sample)
--------------------------------------
1. Location (OED ``LocDed6All``/``LocDedType6All``/``LocLimit6All`` on combined coverages):
     ded = {0: amount, 1: fraction of loss, 2: fraction of TIV}
     net_loc = min(max(GU - ded, 0), limit)          (limit 0 = unlimited)
2. Account (one policy, one layer): X = max(sum(net_loc) - PolDed6All, 0)
     gross_acc = min(max(X - attachment, 0), limit) * participation
   Gross is back-allocated to locations pro rata to net_loc (needed for location AAL, segment
   EP curves and map drill-down).
3. Reinsurance on the portfolio event loss (in the YLT, because aggregate terms are annual):
     quota share q, then per-occurrence XoL layers, all on the same QS-retained (1 - q) * gross
     (layers stack by retention), each with
     an annual aggregate cap limit * (1 + reinstatements). net = gross - QS - sum(XoL).
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
from pydantic import Field

from catmod.config import StrictModel

F64 = npt.NDArray[np.float64]
I64 = npt.NDArray[np.int64]


class XoLLayer(StrictModel):
    name: str = "Cat XoL"
    retention: float = Field(ge=0)
    limit: float = Field(gt=0)
    reinstatements: int = Field(default=1, ge=0, le=20)


class ReinsuranceConfig(StrictModel):
    quota_share: float = Field(default=0.0, ge=0, le=1)
    layers: list[XoLLayer] = Field(default_factory=list)


def location_net(gu: F64, tiv: F64, ded: F64, ded_type: I64, limit: F64) -> F64:
    """gu: (S, n). Returns net-of-location-terms loss (S, n)."""
    ded_amt = np.where(ded_type == 2, ded * tiv, np.where(ded_type == 1, 0.0, ded))
    net = np.maximum(gu - ded_amt, 0.0)
    pct = ded_type == 1
    if pct.any():
        net[:, pct] = gu[:, pct] * (1.0 - ded[pct])
    lim = np.where(limit > 0, limit, np.inf)
    out: F64 = np.minimum(net, lim)
    return out


def account_gross(
    net: F64,
    acc_local: I64,
    n_acc: int,
    acc_ded: F64,
    attachment: F64,
    limit: F64,
    participation: F64,
) -> tuple[F64, F64]:
    """Aggregate to accounts and apply the layer.

    net: (S, n) location net losses; acc_local: (n,) account index in [0, n_acc).
    Returns (gross per location (S, n), gross per account (S, n_acc)).
    """
    s = net.shape[0]
    flat = acc_local[None, :] + n_acc * np.arange(s)[:, None]
    acc_sum = np.bincount(flat.ravel(), weights=net.ravel(), minlength=s * n_acc).reshape(s, n_acc)
    x = np.maximum(acc_sum - acc_ded, 0.0)
    layer = np.minimum(np.maximum(x - attachment, 0.0), limit) * participation
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(acc_sum > 0, layer / acc_sum, 0.0)
    loc_gross: F64 = net * np.take_along_axis(ratio, np.broadcast_to(acc_local, net.shape), 1)
    return loc_gross, layer


def apply_reinsurance(
    gross: F64, year: I64, n_years: int, cfg: ReinsuranceConfig
) -> tuple[F64, F64]:
    """Occurrence-level ceded and net, honouring annual aggregate caps.

    gross: occurrence losses ordered by (year, occurrence order). Returns (ceded, net).
    """
    q = cfg.quota_share
    base = (1.0 - q) * gross  # XoL layers stack on the QS-retained loss (QS inures to XoL)
    ceded = q * gross
    for layer in cfg.layers:
        rec = np.clip(base - layer.retention, 0.0, layer.limit)
        cap = layer.limit * (1 + layer.reinstatements)
        # Cumulative recoveries within each year, then clip by the annual aggregate cap.
        cum = np.cumsum(rec)
        start = np.searchsorted(year, year, side="left")
        prior = np.where(start > 0, cum[start - 1], 0.0)
        within = cum - prior
        allowed = np.clip(cap - (within - rec), 0.0, None)
        rec = np.minimum(rec, allowed)
        ceded = ceded + rec
    return ceded, gross - ceded
