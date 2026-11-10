"""Declarative, validated configuration for synthetic exposure generation.

Every model here is exported as JSON Schema (see ``catmod.schema_export``) so YAML configs can
be validated by editors, the API and the web UI from a single source of truth.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from catmod import oed

Weight = Annotated[float, Field(gt=0)]
Mix = dict[int, Weight]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_mix(mix: Mix, vocab: dict[int, str], what: str) -> Mix:
    unknown = sorted(set(mix) - set(vocab))
    if unknown:
        raise ValueError(f"unknown {what} code(s) {unknown}; known: {sorted(vocab)}")
    if not mix:
        raise ValueError(f"{what} mix must not be empty")
    return mix


class Hub(StrictModel):
    """A population centre around which locations are scattered.

    Radial distance from the centre follows Clark's exponential urban density law
    D(r) = D0 exp(-r/s). In 2-D the radial pdf is proportional to r exp(-r/s), i.e.
    r ~ Gamma(shape=2, scale=s), so ``scale_km`` is the density decay length s.
    """

    name: str = Field(min_length=1)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    weight: Weight = Field(description="Relative share of portfolio locations at this hub")
    scale_km: float = Field(default=12.0, gt=0, le=500, description="Density decay length (km)")
    country: str = Field(default="US", pattern=r"^[A-Z]{2}$")
    perils: list[str] = Field(min_length=1, description="OED peril codes covered at this hub")
    ded_pct_tiv: float = Field(default=0.02, ge=0, le=0.5, description="Location deductible, %TIV")
    value_index: float = Field(default=1.0, gt=0, description="Replacement-cost multiplier")
    vs30: float = Field(
        default=400.0, gt=100, le=2000, description="Median site Vs30 (m/s) for EQ site terms"
    )
    construction_override: dict[int, Mix] = Field(
        default_factory=dict,
        description="Per-occupancy construction mix overriding the global one at this hub",
    )

    @field_validator("perils")
    @classmethod
    def _known_perils(cls, v: list[str]) -> list[str]:
        unknown = sorted(set(v) - set(oed.PERILS))
        if unknown:
            raise ValueError(f"unknown peril code(s) {unknown}; known: {sorted(oed.PERILS)}")
        return v

    @field_validator("construction_override")
    @classmethod
    def _known_override(cls, v: dict[int, Mix]) -> dict[int, Mix]:
        if v:
            _check_mix(dict.fromkeys(v, 1.0), oed.OCCUPANCY, "occupancy")
        for mix in v.values():
            _check_mix(mix, oed.CONSTRUCTION, "construction")
        return v


class Storeys(StrictModel):
    """Storey count = min + Geometric(p) - 1, truncated at max."""

    min: int = Field(default=1, ge=1)
    p: float = Field(gt=0, le=1, description="Geometric success probability")
    max: int = Field(ge=1, le=200)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.max < self.min:
            raise ValueError("max must be >= min")
        return self


class OccupancyClass(StrictModel):
    code: int
    weight: Weight
    construction: Mix
    storeys: Storeys
    value_per_storey: float = Field(gt=0, description="Median building value per storey")
    value_sigma: float = Field(default=0.5, ge=0, le=3, description="Lognormal sigma of value")
    contents_ratio: float = Field(ge=0, le=10, description="Mean contents TIV / building TIV")
    bi_ratio: float = Field(ge=0, le=10, description="Mean BI TIV / building TIV")
    personal_lines: bool = Field(
        default=False, description="Each location is its own single-location account"
    )

    @field_validator("code")
    @classmethod
    def _known_code(cls, v: int) -> int:
        _check_mix({v: 1.0}, oed.OCCUPANCY, "occupancy")
        return v

    @field_validator("construction")
    @classmethod
    def _known_construction(cls, v: Mix) -> Mix:
        return _check_mix(v, oed.CONSTRUCTION, "construction")


class YearBuilt(StrictModel):
    mean: float = 1985
    sd: float = Field(default=20, ge=0)
    min: int = 1850
    max: int = 2026


class Accounts(StrictModel):
    """Commercial schedules: account size - 1 ~ Lomax(alpha) scaled to the requested mean."""

    commercial_mean_locations: float = Field(default=6.0, gt=1)
    size_tail_alpha: float = Field(default=1.6, gt=1, description="Pareto tail index of size")
    participation: tuple[float, float] = (0.25, 1.0)
    limit_ratio: tuple[float, float] = Field(
        default=(0.3, 1.0), description="Layer limit as a fraction of account TIV"
    )
    attachment_ratio: tuple[float, float] = Field(
        default=(0.0, 0.05), description="Layer attachment as a fraction of account TIV"
    )

    @field_validator("participation", "limit_ratio", "attachment_ratio")
    @classmethod
    def _range(cls, v: tuple[float, float]) -> tuple[float, float]:
        lo, hi = v
        if not 0 <= lo <= hi <= 1:
            raise ValueError("range must satisfy 0 <= lo <= hi <= 1")
        return v


class PortfolioConfig(StrictModel):
    """Top-level synthetic portfolio specification."""

    name: str = Field(min_length=1, max_length=120)
    seed: int = Field(default=42, ge=0)
    n_locations: int = Field(ge=1, le=5_000_000)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    portfolio_number: str = Field(default="P1", min_length=1)
    hubs: list[Hub] = Field(min_length=1)
    occupancies: list[OccupancyClass] = Field(min_length=1)
    year_built: YearBuilt = YearBuilt()
    accounts: Accounts = Accounts()
    urban_value_uplift: float = Field(
        default=0.5, ge=0, description="Value premium at the hub core, decaying with exp(-r/s)"
    )
    h3_resolutions: list[int] = Field(default=[5, 7], min_length=1)

    @field_validator("h3_resolutions")
    @classmethod
    def _h3(cls, v: list[int]) -> list[int]:
        if any(not 0 <= r <= 15 for r in v):
            raise ValueError("H3 resolutions must be in [0, 15]")
        return sorted(set(v))

    @model_validator(mode="after")
    def _unique(self) -> Self:
        codes = [o.code for o in self.occupancies]
        if len(codes) != len(set(codes)):
            raise ValueError("occupancy codes must be unique")
        names = [h.name for h in self.hubs]
        if len(names) != len(set(names)):
            raise ValueError("hub names must be unique")
        return self


# --------------------------------------------------------------------------- presets / IO

_PRESET_PKG = "catmod.presets"


def list_presets() -> list[str]:
    return sorted(
        p.name.removesuffix(".yaml")
        for p in resources.files(_PRESET_PKG).iterdir()
        if p.name.endswith(".yaml")
    )


def load_preset(name: str) -> PortfolioConfig:
    if name not in list_presets():
        raise KeyError(f"unknown preset {name!r}; available: {list_presets()}")
    text = resources.files(_PRESET_PKG).joinpath(f"{name}.yaml").read_text()
    return PortfolioConfig.model_validate(yaml.safe_load(text))


def load_config(path: Path) -> PortfolioConfig:
    return PortfolioConfig.model_validate(yaml.safe_load(path.read_text()))
