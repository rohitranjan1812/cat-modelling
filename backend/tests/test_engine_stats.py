"""Statistical validation of the pluggable stochastic components."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import special, stats

from catmod.engine.copulas import OneFactorCopula, SpatialCopula, make_copula
from catmod.engine.fields import CholeskyField, FFTField, IndependentField, make_field
from catmod.engine.registry import describe
from catmod.engine.vulnerability import Vulnerability, VulnerabilityConfig


def _pairs(n: int, spacing: float, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    x = rng.uniform(0, n * spacing, n)
    y = rng.uniform(0, n * spacing, n)
    return x, y


@pytest.mark.parametrize("model", ["fft", "cholesky"])
def test_field_marginals_and_correlation(model: str) -> None:
    rng = np.random.default_rng(1)
    rng_km = 20.0
    # two sites at fixed lags, many realisations
    lags = np.array([0.0, 5.0, 10.0, 20.0, 40.0])
    x = np.concatenate([[0.0], lags[1:]])
    y = np.zeros_like(x)
    f = make_field(model, range_km=rng_km, grid_km=1.0)
    z = f.sample(rng, x, y, 4000)
    assert abs(z.mean()) < 0.05
    assert np.allclose(z.std(axis=0), 1.0, atol=0.06)
    emp = np.array([np.corrcoef(z[:, 0], z[:, i])[0, 1] for i in range(1, x.size)])
    target = np.exp(-3 * lags[1:] / rng_km)
    assert np.allclose(emp, target, atol=0.05), (emp, target)


def test_fft_matches_cholesky_on_random_sites() -> None:
    """Empirical correlation matrices of the two models agree (FFT is exact at grid nodes)."""
    rng = np.random.default_rng(2)
    x, y = rng.uniform(0, 60, 40), rng.uniform(0, 60, 40)
    x, y = np.round(x), np.round(y)  # put sites on the 1 km grid
    zf = FFTField(range_km=15, grid_km=1.0).sample(rng, x, y, 6000)
    zc = CholeskyField(range_km=15).sample(rng, x, y, 6000)
    diff = np.abs(np.corrcoef(zf.T) - np.corrcoef(zc.T))
    assert diff.max() < 0.08
    assert diff.mean() < 0.02


def test_independent_field_is_uncorrelated() -> None:
    rng = np.random.default_rng(3)
    z = IndependentField().sample(rng, np.zeros(3), np.zeros(3), 5000)
    c = np.corrcoef(z.T)
    assert np.abs(c[np.triu_indices(3, 1)]).max() < 0.05


def test_cholesky_size_guard() -> None:
    with pytest.raises(ValueError, match="use 'fft'"):
        CholeskyField(max_sites=10).sample(np.random.default_rng(0), np.zeros(11), np.zeros(11), 1)


def test_one_factor_pairwise_correlation_by_class() -> None:
    rng = np.random.default_rng(4)
    cop = OneFactorCopula(rho=0.2, rho_by_construction={5150: 0.5})
    cons = np.array([5050, 5050, 5150, 5150])
    z = cop.sample(rng, np.zeros(4), np.zeros(4), cons, 20_000)
    c = np.corrcoef(z.T)
    assert c[0, 1] == pytest.approx(0.2, abs=0.03)
    assert c[2, 3] == pytest.approx(0.5, abs=0.03)
    assert c[0, 2] == pytest.approx(np.sqrt(0.2 * 0.5), abs=0.03)


def test_spatial_copula_decays_with_distance() -> None:
    rng = np.random.default_rng(5)
    cop = SpatialCopula(rho=0.6, range_km=10.0)
    x = np.array([0.0, 1.0, 30.0])
    z = cop.sample(rng, x, np.zeros(3), np.zeros(3, dtype=np.int64), 8000)
    c = np.corrcoef(z.T)
    assert c[0, 1] == pytest.approx(0.6 * np.exp(-0.3), abs=0.06)
    assert abs(c[0, 2]) < 0.06


def test_beta_table_moments() -> None:
    v = Vulnerability(VulnerabilityConfig(secondary_k=0.4))
    rng = np.random.default_rng(6)
    for m in (0.01, 0.1, 0.5, 0.9):
        d = v.sample(np.full(200_000, m), rng.standard_normal(200_000))
        assert d.mean() == pytest.approx(m, rel=0.02, abs=2e-4)
        assert d.std() == pytest.approx(0.4 * np.sqrt(m * (1 - m)), rel=0.05)
        assert d.min() >= 0 and d.max() <= 1


def test_beta_table_matches_exact_inverse() -> None:
    v = Vulnerability()
    rng = np.random.default_rng(7)
    m = special.expit(rng.uniform(-8, 4, 2000))
    z = rng.uniform(-4, 4, 2000)
    nu = 1 / 0.4**2 - 1
    exact = special.betaincinv(m * nu, (1 - m) * nu, special.ndtr(z))
    assert np.abs(v.sample(m, z) - exact).max() < 0.01


@pytest.mark.parametrize("rho", [0.1, 0.4])
def test_one_factor_large_portfolio_limit(rho: float) -> None:
    """Generalised Vasicek limit: for a homogeneous portfolio of N -> inf sites,
    loss ratio | Z = g(Z) = E_xi[F^-1(Phi(sqrt(rho) Z + sqrt(1-rho) xi))], so the q-quantile of
    the loss ratio is g(Phi^-1(q)) (g is increasing). Checked by Gauss-Hermite quadrature."""
    v = Vulnerability()
    # 20k scenarios: quantile MC error ~1%; 2k sites: idiosyncratic noise ~ sd/sqrt(n) small
    m, n_sites, n_sims = 0.05, 2000, 20_000
    rng = np.random.default_rng(8)
    cop = OneFactorCopula(rho=rho)
    cons = np.zeros(n_sites, dtype=np.int64)
    zeros = np.zeros(n_sites)
    ratios = np.empty(n_sims)
    for i in range(0, n_sims, 1000):
        z = cop.sample(rng, zeros, zeros, cons, 1000)
        ratios[i : i + 1000] = v.sample(np.full(z.shape, m), z).mean(axis=1)
    gh_x, gh_w = np.polynomial.hermite_e.hermegauss(80)
    gh_w = gh_w / gh_w.sum()

    def g(zf: float) -> float:
        s = np.sqrt(rho) * zf + np.sqrt(1 - rho) * gh_x
        return float((v.sample(np.full(s.size, m), s) * gh_w).sum())

    for q in (0.5, 0.9, 0.99):
        assert np.quantile(ratios, q) == pytest.approx(g(stats.norm.ppf(q)), rel=0.035)


def test_copula_does_not_change_mean_damage() -> None:
    """AAL invariance: every copula preserves the marginal, hence the mean damage ratio."""
    v = Vulnerability()
    rng = np.random.default_rng(9)
    n = 2000
    x, y = rng.uniform(0, 50, n), rng.uniform(0, 50, n)
    cons = np.full(n, 5050)
    means = {}
    for name in describe()["copulas"]:
        z = make_copula(name).sample(rng, x, y, cons, 400)
        means[name] = v.sample(np.full(z.shape, 0.08), z).mean()
    assert max(means.values()) - min(means.values()) < 0.004, means
