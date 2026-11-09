from __future__ import annotations

from collections import Counter

import numpy as np
import pyarrow.compute as pc
import pytest
from pydantic import ValidationError

from catmod import oed
from catmod.config import PortfolioConfig, load_preset
from catmod.exposure import generate
from catmod.exposure.io import summarize, write_portfolio
from catmod.exposure.synthetic import _commercial_account_sizes
from catmod.geo import haversine_km

N = 20_000


@pytest.fixture(scope="module")
def cfg() -> PortfolioConfig:
    return load_preset("us-cat-demo").model_copy(update={"n_locations": N})


@pytest.fixture(scope="module")
def portfolio(cfg: PortfolioConfig):  # type: ignore[no-untyped-def]
    return generate(cfg)


def test_deterministic(cfg: PortfolioConfig, portfolio) -> None:  # type: ignore[no-untyped-def]
    again = generate(cfg)
    assert again.locations.equals(portfolio.locations)
    assert again.accounts.equals(portfolio.accounts)
    other = generate(cfg.model_copy(update={"seed": cfg.seed + 1}))
    assert not other.locations.equals(portfolio.locations)


def test_oed_schema(portfolio) -> None:  # type: ignore[no-untyped-def]
    oed_cols = [f.name for f in oed.LOCATION_FIELDS]
    assert portfolio.locations.schema.names[: len(oed_cols)] == oed_cols
    assert portfolio.accounts.schema == oed.ACCOUNT_SCHEMA
    assert portfolio.locations.num_rows == N
    for col in ("BuildingTIV", "ContentsTIV", "BITIV"):
        assert pc.min(portfolio.locations[col]).as_py() >= 0
    assert pc.min(portfolio.locations["BuildingTIV"]).as_py() > 0


def test_referential_integrity(portfolio) -> None:  # type: ignore[no-untyped-def]
    loc_accs = set(portfolio.locations["AccNumber"].to_pylist())
    acc_accs = portfolio.accounts["AccNumber"].to_pylist()
    assert len(acc_accs) == len(set(acc_accs))
    assert loc_accs == set(acc_accs)
    assert len(set(portfolio.locations["LocNumber"].to_pylist())) == N


def test_policy_perils_cover_locations(portfolio) -> None:  # type: ignore[no-untyped-def]
    pol = dict(
        zip(
            portfolio.accounts["AccNumber"].to_pylist(),
            portfolio.accounts["PolPerilsCovered"].to_pylist(),
            strict=True,
        )
    )
    for acc, perils in zip(
        portfolio.locations["AccNumber"].to_pylist(),
        portfolio.locations["LocPerilsCovered"].to_pylist(),
        strict=True,
    ):
        assert set(perils.split(";")) <= set(pol[acc].split(";"))


def test_occupancy_mix_matches_weights(cfg: PortfolioConfig, portfolio) -> None:  # type: ignore[no-untyped-def]
    counts = Counter(portfolio.locations["OccupancyCode"].to_pylist())
    total_w = sum(o.weight for o in cfg.occupancies)
    for o in cfg.occupancies:
        p = o.weight / total_w
        se = np.sqrt(p * (1 - p) / N)
        assert abs(counts[o.code] / N - p) < 5 * se


def test_construction_override_applies(portfolio) -> None:  # type: ignore[no-untyped-def]
    t = portfolio.locations
    hub = np.array(t["FlexiLocHub"].to_pylist())
    occ = t["OccupancyCode"].to_numpy()
    con = t["ConstructionCode"].to_numpy()
    miami_sfd = (hub == "Miami") & (occ == 1051)
    la_sfd = (hub == "Los Angeles") & (occ == 1051)
    assert (con[miami_sfd] == 5100).mean() > 0.65  # FL masonry
    assert (con[la_sfd] == 5050).mean() > 0.70  # CA wood


def test_clark_radial_distribution(cfg: PortfolioConfig, portfolio) -> None:  # type: ignore[no-untyped-def]
    """r ~ Gamma(2, s): mean 2s. Check via great-circle distance back to the hub."""
    t = portfolio.locations
    hubs = {h.name: h for h in cfg.hubs}
    name = "Los Angeles"
    mask = np.array(t["FlexiLocHub"].to_pylist()) == name
    lat, lon = t["Latitude"].to_numpy()[mask], t["Longitude"].to_numpy()[mask]
    h = hubs[name]
    d = haversine_km(np.full_like(lat, h.lat), np.full_like(lon, h.lon), lat, lon)
    se = np.sqrt(2) * h.scale_km / np.sqrt(d.size)
    assert abs(d.mean() - 2 * h.scale_km) < 5 * se


def test_commercial_account_sizes() -> None:
    rng = np.random.default_rng(0)
    sizes = _commercial_account_sizes(rng, 100_000, mean=6.0, alpha=1.6)
    assert sizes.sum() == 100_000
    assert sizes.min() >= 1
    assert 4.0 < sizes.mean() < 8.0  # heavy tail => noisy mean, but near target
    assert sizes.max() > 50  # tail present


def test_summary_and_io(portfolio, tmp_path) -> None:  # type: ignore[no-untyped-def]
    s = summarize(portfolio.locations, portfolio.accounts)
    assert s.n_locations == N
    assert s.tiv_total == pytest.approx(s.tiv_building + s.tiv_contents + s.tiv_bi)
    assert {p.peril for p in s.by_peril} == {"QEQ", "WTC"}
    paths = write_portfolio(portfolio, tmp_path, ("parquet", "csv"))
    assert {p.name for p in paths} == {
        "location.parquet",
        "account.parquet",
        "location.csv",
        "account.csv",
    }
    header = (tmp_path / "location.csv").read_text().splitlines()[0]
    assert "catmod_" not in header


@pytest.mark.parametrize(
    "patch",
    [
        {"n_locations": 0},
        {"currency": "usd"},
        {"h3_resolutions": [16]},
        {"hubs": []},
    ],
)
def test_config_rejects_invalid(cfg: PortfolioConfig, patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PortfolioConfig.model_validate({**cfg.model_dump(), **patch})


def test_config_rejects_unknown_codes(cfg: PortfolioConfig) -> None:
    data = cfg.model_dump()
    data["hubs"][0]["perils"] = ["XXX"]
    with pytest.raises(ValidationError, match="unknown peril"):
        PortfolioConfig.model_validate(data)
    data = cfg.model_dump()
    data["occupancies"][0]["construction"] = {9999: 1.0}
    with pytest.raises(ValidationError, match="unknown construction"):
        PortfolioConfig.model_validate(data)
