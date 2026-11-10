"""Spatially correlated standard-normal random fields (pluggable).

All models target the isotropic exponential correlation rho(h) = exp(-3 h / range), the form of
Jayaram & Baker (2009) for ground-motion within-event residuals ("range" is the practical range
where correlation falls to ~0.05).

Models
------
``independent``  rho(h) = 1{h = 0}. O(n). Lower bound on spatial clustering.
``cholesky``     Exact: L L^T = C, X = Z L^T. O(n^3) — only for small n and validation.
``fft``          Circulant embedding on a regular grid (Dietrich & Newsam 1997). The covariance
                 is wrapped onto a torus of size M >= N + 1.5 range, whose covariance matrix is
                 block-circulant and therefore diagonalised by the 2-D DFT. One complex FFT of
                 sqrt(lambda) * (xi1 + i xi2) yields TWO independent exact fields (real and
                 imaginary parts). O(M^2 log M) per pair, independent of the number of sites.
                 Sites read their nearest grid node, so correlation is exact at node spacing.
                 The torus is padded by 1.5 x range (not the classical 2N): wrapped lags are then
                 >= 1.5 range, where rho <= exp(-4.5) ~ 1%, cutting FFT work up to 4x for large
                 footprints at a bounded, tested correlation error.
"""

from __future__ import annotations

from functools import lru_cache
from typing import ClassVar, Protocol

import numpy as np
import numpy.typing as npt
from scipy import fft as sfft

F64 = npt.NDArray[np.float64]


class SpatialField(Protocol):
    name: ClassVar[str]

    def sample(self, rng: np.random.Generator, x_km: F64, y_km: F64, n_samples: int) -> F64:
        """Return an (n_samples, n_sites) array of standard-normal correlated values."""
        ...


def exp_corr(h_km: F64, range_km: float) -> F64:
    out: F64 = np.exp(-3.0 * h_km / range_km)
    return out


class IndependentField:
    name: ClassVar[str] = "independent"

    def __init__(self, range_km: float = 0.0, **_: object) -> None:
        self.range_km = range_km

    def sample(self, rng: np.random.Generator, x_km: F64, y_km: F64, n_samples: int) -> F64:
        return rng.standard_normal((n_samples, x_km.size))


class CholeskyField:
    name: ClassVar[str] = "cholesky"

    def __init__(self, range_km: float = 20.0, max_sites: int = 4000, **_: object) -> None:
        self.range_km = range_km
        self.max_sites = max_sites

    def sample(self, rng: np.random.Generator, x_km: F64, y_km: F64, n_samples: int) -> F64:
        n = x_km.size
        if n > self.max_sites:
            raise ValueError(
                f"cholesky field limited to {self.max_sites} sites (got {n}); use 'fft'"
            )
        if n == 0:
            return np.zeros((n_samples, 0))
        d = np.hypot(x_km[:, None] - x_km[None, :], y_km[:, None] - y_km[None, :])
        c = exp_corr(d, self.range_km)
        c[np.diag_indices(n)] += 1e-10
        chol = np.linalg.cholesky(c)
        out: F64 = rng.standard_normal((n_samples, n)) @ chol.T
        return out


@lru_cache(maxsize=64)
def _sqrt_eigs(mx: int, my: int, dx: float, range_km: float) -> F64:
    ix = np.minimum(np.arange(mx), mx - np.arange(mx)) * dx
    iy = np.minimum(np.arange(my), my - np.arange(my)) * dx
    h = np.hypot(ix[:, None], iy[None, :])
    lam = np.real(sfft.fft2(exp_corr(h, range_km)))
    # Exponential kernel with padding >= range is (numerically) PSD; clip round-off.
    out: F64 = np.sqrt(np.clip(lam, 0.0, None) / (mx * my))
    return out


class FFTField:
    name: ClassVar[str] = "fft"

    def __init__(self, range_km: float = 20.0, grid_km: float | None = None, **_: object) -> None:
        self.range_km = range_km
        self.grid_km = grid_km if grid_km is not None else max(0.5, range_km / 8.0)

    def sample(self, rng: np.random.Generator, x_km: F64, y_km: F64, n_samples: int) -> F64:
        n = x_km.size
        if n == 0:
            return np.zeros((n_samples, 0))
        dx = self.grid_km
        x0, y0 = float(np.min(x_km)), float(np.min(y_km))
        ix = np.rint((x_km - x0) / dx).astype(np.int64)
        iy = np.rint((y_km - y0) / dx).astype(np.int64)
        nx, ny = int(np.max(ix)) + 1, int(np.max(iy)) + 1
        pad = int(np.ceil(1.5 * self.range_km / dx))
        mx = sfft.next_fast_len(nx + pad)
        my = sfft.next_fast_len(ny + pad)
        root = _sqrt_eigs(mx, my, dx, self.range_km)
        out = np.empty((n_samples, n))
        for k in range(0, n_samples, 2):
            w = rng.standard_normal((mx, my)) + 1j * rng.standard_normal((mx, my))
            y = sfft.fft2(root * w, workers=-1)
            out[k] = y.real[ix, iy]
            if k + 1 < n_samples:
                out[k + 1] = y.imag[ix, iy]
        return out


FIELDS: dict[str, type[IndependentField] | type[CholeskyField] | type[FFTField]] = {
    IndependentField.name: IndependentField,
    CholeskyField.name: CholeskyField,
    FFTField.name: FFTField,
}


def make_field(model: str, **kwargs: object) -> SpatialField:
    from catmod.engine.registry import spatial_fields

    registry = spatial_fields()
    if model not in registry:
        raise KeyError(f"unknown spatial field model {model!r}; available: {sorted(registry)}")
    field: SpatialField = registry[model](**kwargs)
    return field
