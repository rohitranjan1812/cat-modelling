from __future__ import annotations

import pytest

from catmod.config import PortfolioConfig, load_preset
from catmod.engine.context import Exposure
from catmod.engine.eq import EqConfig
from catmod.engine.runner import AnalysisConfig
from catmod.engine.tc import TcConfig
from catmod.exposure import generate


def small_analysis(**kw: object) -> AnalysisConfig:
    """Coarse catalogue so engine tests run in seconds."""
    base = {
        "n_samples": 8,
        "n_years": 5_000,
        "eq": EqConfig(dm=0.5, position_step_km=60, area_sites=3),
        "tc": TcConfig(
            gate_spacing_km=300,
            vmax_step=15,
            heading_offsets_deg=[0.0],
            heading_weights=[1.0],
            rmax_factors=[1.0],
            rmax_weights=[1.0],
        ),
        "workers": 1,
    }
    base.update(kw)
    return AnalysisConfig.model_validate(base)


@pytest.fixture(scope="session")
def small_cfg() -> PortfolioConfig:
    return load_preset("us-cat-demo").model_copy(update={"n_locations": 3_000})


@pytest.fixture(scope="session")
def exposure(small_cfg: PortfolioConfig) -> Exposure:
    p = generate(small_cfg)
    return Exposure.from_tables(p.locations, p.accounts)
