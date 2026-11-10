"""Damage-correlation copulas (pluggable).

Each model returns standard-normal scores z (n_samples, n_sites); damage is then
F^{-1}_{Beta}(Phi(z)) per site, so the copula changes the *joint* behaviour of damage across
locations while leaving every marginal (and therefore AAL) untouched.

Models
------
``independent``     z = xi.
``one_factor``      z_i = sqrt(rho_i) * Z + sqrt(1 - rho_i) * xi_i, a single event-level factor;
                    rho_i may vary by construction class. Closed-form large-portfolio limit
                    (Vasicek-type):
                    loss ratio | Z -> E_xi[F^{-1}(Phi(sqrt(rho) Z + sqrt(1-rho) xi))].
``spatial``         z_i = sqrt(rho) * G(x_i) + sqrt(1 - rho) * xi_i with G an exponential-correlated
                    Gaussian field: corr(z_i, z_j) = rho * exp(-3 h_ij / range). Correlation decays
                    with distance, so neighbouring buildings co-move but distant hubs do not.
"""

from __future__ import annotations

from typing import ClassVar, Protocol

import numpy as np
import numpy.typing as npt

from catmod.engine.fields import make_field

F64 = npt.NDArray[np.float64]
I64 = npt.NDArray[np.int64]


class DamageCopula(Protocol):
    name: ClassVar[str]

    def sample(
        self,
        rng: np.random.Generator,
        x_km: F64,
        y_km: F64,
        construction: I64,
        n_samples: int,
    ) -> F64: ...


class IndependentCopula:
    name: ClassVar[str] = "independent"

    def __init__(self, **_: object) -> None:
        pass

    def sample(
        self, rng: np.random.Generator, x_km: F64, y_km: F64, construction: I64, n_samples: int
    ) -> F64:
        return rng.standard_normal((n_samples, x_km.size))


class OneFactorCopula:
    name: ClassVar[str] = "one_factor"

    def __init__(
        self, rho: float = 0.15, rho_by_construction: dict[int, float] | None = None, **_: object
    ) -> None:
        self.rho = rho
        self.rho_by_construction = rho_by_construction or {}

    def _rho(self, construction: I64) -> F64:
        r = np.full(construction.size, self.rho)
        for code, val in self.rho_by_construction.items():
            r[construction == int(code)] = val
        return r

    def sample(
        self, rng: np.random.Generator, x_km: F64, y_km: F64, construction: I64, n_samples: int
    ) -> F64:
        r = self._rho(construction)
        common = rng.standard_normal((n_samples, 1))
        idio = rng.standard_normal((n_samples, x_km.size))
        out: F64 = np.sqrt(r) * common + np.sqrt(1.0 - r) * idio
        return out


class SpatialCopula:
    name: ClassVar[str] = "spatial"

    def __init__(
        self, rho: float = 0.3, range_km: float = 10.0, field: str = "fft", **_: object
    ) -> None:
        self.rho = rho
        self.field = make_field(field, range_km=range_km)

    def sample(
        self, rng: np.random.Generator, x_km: F64, y_km: F64, construction: I64, n_samples: int
    ) -> F64:
        g = self.field.sample(rng, x_km, y_km, n_samples)
        idio = rng.standard_normal(g.shape)
        out: F64 = np.sqrt(self.rho) * g + np.sqrt(1.0 - self.rho) * idio
        return out


def make_copula(model: str, **kwargs: object) -> DamageCopula:
    from catmod.engine.registry import copulas

    registry = copulas()
    if model not in registry:
        raise KeyError(f"unknown damage copula {model!r}; available: {sorted(registry)}")
    c: DamageCopula = registry[model](**kwargs)
    return c
