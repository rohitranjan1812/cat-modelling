"""Financial algebra, metrics and end-to-end run invariants."""

from __future__ import annotations

import numpy as np
import pytest

from catmod.engine.context import Exposure
from catmod.engine.financial import (
    ReinsuranceConfig,
    XoLLayer,
    account_gross,
    apply_reinsurance,
    location_net,
)
from catmod.engine.metrics import perspective_metrics, rp_value, tvar
from catmod.engine.runner import Engine, run_analysis

from .conftest import small_analysis


def test_location_terms() -> None:
    gu = np.array([[100.0, 100.0, 100.0, 100.0]])
    tiv = np.array([1000.0, 1000.0, 1000.0, 1000.0])
    ded = np.array([30.0, 0.2, 0.05, 0.0])
    typ = np.array([0, 1, 2, 0])
    lim = np.array([0.0, 0.0, 0.0, 60.0])
    assert location_net(gu, tiv, ded, typ, lim).tolist() == [[70.0, 80.0, 50.0, 60.0]]


def test_account_layer_and_allocation() -> None:
    net = np.array([[60.0, 40.0, 10.0]])
    acc = np.array([0, 0, 1])
    loc, layer = account_gross(
        net,
        acc,
        2,
        np.array([0.0, 0.0]),
        np.array([20.0, 0.0]),
        np.array([50.0, 100.0]),
        np.array([0.5, 1.0]),
    )
    # acc0: min(max(100-20,0),50)*0.5 = 25 ; acc1: 10
    assert layer.tolist() == [[25.0, 10.0]]
    assert loc.sum() == pytest.approx(35.0)
    assert loc[0, 0] / loc[0, 1] == pytest.approx(60 / 40)


def test_reinsurance_qs_xol_and_aggregate_cap() -> None:
    gross = np.array([100.0, 100.0, 100.0, 50.0])
    year = np.array([0, 0, 0, 1])
    cfg = ReinsuranceConfig(
        quota_share=0.2, layers=[XoLLayer(retention=40, limit=30, reinstatements=1)]
    )
    ceded, net = apply_reinsurance(gross, year, 2, cfg)
    # retained after QS = 80 -> XoL recovers 30 per event, capped at 60 in year 0
    assert ceded.tolist() == [50.0, 50.0, 20.0, 10.0]
    assert np.allclose(ceded + net, gross)


def test_metrics_basics() -> None:
    v = np.arange(1, 1001, dtype=float)  # 1000 years
    assert rp_value(v, 100) == 991.0
    assert rp_value(v, 1) == 1.0
    var, tv = tvar(v, 0.99)
    assert var == 991.0 and tv == pytest.approx(995.5)
    m = perspective_metrics(np.arange(1000), v, 1000, [10, 100], [0.99], n_batches=10)
    assert m.aal == pytest.approx(500.5)
    assert m.oep["100"] == m.aep["100"] == 991.0
    assert m.oep_se["100"] is not None and m.oep_se["10"] is not None


def test_event_recompute_is_deterministic(exposure: Exposure) -> None:
    eng = Engine(exposure, small_analysis())
    ev = max(eng.catalog(), key=lambda e: eng.hazards[e.peril].footprint(e, exposure).idx.size)
    a, b = eng.event_loss(ev), eng.event_loss(ev)
    assert np.array_equal(a.gu, b.gu) and np.array_equal(a.loc_gross, b.loc_gross)
    assert a.gu.max() > 0
    # gross <= GU per sample
    assert np.all(a.gross <= a.gu + 1e-6)


def test_run_invariants(exposure: Exposure) -> None:
    cfg = small_analysis(
        reinsurance=ReinsuranceConfig(quota_share=0.1, layers=[XoLLayer(retention=5e7, limit=2e8)])
    )
    r = run_analysis(exposure, cfg)
    m = r.summary.metrics
    assert r.summary.n_loss_events > 0
    for p in ("gu", "gross", "net"):
        assert m[p].aal > 0
    assert m["net"].aal < m["gross"].aal <= m["gu"].aal
    # simulated AAL converges to the analytic ELT AAL
    assert abs(m["gu"].aal - r.summary.aal_elt["gu"]) < 4 * (m["gu"].aal_se or 0) + 1e-9
    # location AAL sums to the ELT AAL exactly
    assert sum(r.loc_aal["aal_gu"].to_pylist()) == pytest.approx(r.summary.aal_elt["gu"])
    # segment losses reconcile with event-sample totals
    seg_total = sum(r.segments["gu"].to_pylist())
    assert seg_total == pytest.approx(sum(r.samples["gu"].to_pylist()), rel=1e-9)
    # EP monotone in return period
    oep = [m["gross"].oep[str(k)] for k in cfg.return_periods]
    assert oep == sorted(oep)


def test_run_independent_of_worker_count(exposure: Exposure) -> None:
    a = run_analysis(exposure, small_analysis(workers=1, n_years=1000))
    b = run_analysis(exposure, small_analysis(workers=2, n_years=1000))
    assert a.summary.metrics["gross"] == b.summary.metrics["gross"]
    assert a.loc_aal["aal_gross"].to_pylist() == pytest.approx(b.loc_aal["aal_gross"].to_pylist())


def test_correlation_models_shift_tail_not_mean(exposure: Exposure) -> None:
    """Pluggable damage copula: AAL invariant (within MC error), tail widens with rho."""
    res = {}
    for model, rho in (("independent", 0.0), ("one_factor", 0.5)):
        cfg = small_analysis(n_samples=32, damage_correlation={"model": model, "rho": rho})
        res[model] = run_analysis(exposure, cfg).summary
    ind, one = res["independent"], res["one_factor"]
    assert one.aal_elt["gu"] == pytest.approx(ind.aal_elt["gu"], rel=0.05)
    assert one.metrics["gu"].sd > ind.metrics["gu"].sd
