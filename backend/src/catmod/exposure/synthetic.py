"""Deterministic synthetic OED portfolio generator.

Design notes
------------
* Fully vectorised NumPy; one ``Generator`` seeded from the config, so a config is a
  reproducible portfolio identity (same config -> bit-identical tables).
* Spatial: hub mixture with Clark-law radial density, r ~ Gamma(2, s) (see ``config.Hub``).
* Values: building TIV = value_per_storey * storeys * hub value index * urban uplift * eps,
  where eps ~ LogNormal(-sigma^2/2, sigma) is mean-one, so medians/means stay interpretable.
* Construction is conditional on (hub, occupancy), allowing regional building practice
  (e.g. Florida masonry homes vs California wood frame).
* Accounts: personal lines are single-location; commercial schedules have heavy-tailed sizes
  (1 + Lomax) so a handful of accounts carry many locations, as in real books.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pyarrow as pa

from catmod import oed
from catmod.config import Mix, PortfolioConfig
from catmod.geo import destination, h3_cells

IntArray = npt.NDArray[np.int64]


@dataclass(frozen=True)
class Portfolio:
    config: PortfolioConfig
    locations: pa.Table
    accounts: pa.Table


def _probs(weights: list[float]) -> npt.NDArray[np.float64]:
    w = np.asarray(weights, dtype=np.float64)
    out: npt.NDArray[np.float64] = w / w.sum()
    return out


def _draw_mix(rng: np.random.Generator, mix: Mix, size: int) -> IntArray:
    codes = np.fromiter(mix.keys(), dtype=np.int64)
    return rng.choice(codes, size=size, p=_probs(list(mix.values())))


def _mean_one_lognormal(
    rng: np.random.Generator, sigma: npt.ArrayLike, size: int
) -> npt.NDArray[np.float64]:
    s = np.asarray(sigma, dtype=np.float64)
    out: npt.NDArray[np.float64] = rng.lognormal(mean=-0.5 * s**2, sigma=s, size=size)
    return out


def _commercial_account_sizes(
    rng: np.random.Generator, n: int, mean: float, alpha: float
) -> IntArray:
    """Partition n locations into accounts with size = 1 + floor(scaled Lomax)."""
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    # Lomax(alpha) has mean 1/(alpha-1); scale so E[size] ~= mean (floor bias ~0.5 corrected).
    scale = (mean - 0.5) * (alpha - 1)
    sizes: list[IntArray] = []
    total = 0
    while total < n:
        batch = 1 + np.floor(rng.pareto(alpha, size=max(16, int(n / mean) + 16)) * scale)
        sizes.append(batch.astype(np.int64))
        total += int(batch.sum())
    out = np.concatenate(sizes)
    cut = int(np.searchsorted(np.cumsum(out), n))
    out = out[: cut + 1].copy()
    out[-1] -= int(out.sum()) - n
    return out


def generate(cfg: PortfolioConfig) -> Portfolio:
    rng = np.random.default_rng(cfg.seed)
    n = cfg.n_locations
    hubs, occs = cfg.hubs, cfg.occupancies

    # ---- space
    hub_idx = rng.choice(len(hubs), size=n, p=_probs([h.weight for h in hubs]))
    hub_lat = np.array([h.lat for h in hubs])[hub_idx]
    hub_lon = np.array([h.lon for h in hubs])[hub_idx]
    scale = np.array([h.scale_km for h in hubs])[hub_idx]
    r_km = rng.gamma(shape=2.0, scale=scale)
    bearing = rng.uniform(0.0, 2.0 * np.pi, size=n)
    lat, lon = destination(hub_lat, hub_lon, bearing, r_km)

    # ---- occupancy and (hub, occupancy)-conditional construction
    occ_idx = rng.choice(len(occs), size=n, p=_probs([o.weight for o in occs]))
    construction = np.empty(n, dtype=np.int64)
    for hi, hub in enumerate(hubs):
        for oi, occ in enumerate(occs):
            mask = (hub_idx == hi) & (occ_idx == oi)
            k = int(mask.sum())
            if k:
                mix = hub.construction_override.get(occ.code, occ.construction)
                construction[mask] = _draw_mix(rng, mix, k)

    # ---- storeys and age
    s_min = np.array([o.storeys.min for o in occs])[occ_idx]
    s_max = np.array([o.storeys.max for o in occs])[occ_idx]
    s_p = np.array([o.storeys.p for o in occs])[occ_idx]
    storeys = np.minimum(s_min + rng.geometric(s_p) - 1, s_max)
    yb = cfg.year_built
    year_built = np.clip(np.rint(rng.normal(yb.mean, yb.sd, size=n)), yb.min, yb.max)

    # ---- values
    vps = np.array([o.value_per_storey for o in occs])[occ_idx]
    sigma = np.array([o.value_sigma for o in occs])[occ_idx]
    value_index = np.array([h.value_index for h in hubs])[hub_idx]
    uplift = 1.0 + cfg.urban_value_uplift * np.exp(-r_km / scale)
    building = np.round(
        vps * storeys * value_index * uplift * _mean_one_lognormal(rng, sigma, n), -2
    )
    contents = np.round(
        building
        * np.array([o.contents_ratio for o in occs])[occ_idx]
        * _mean_one_lognormal(rng, 0.25, n),
        -2,
    )
    bi = np.round(
        building * np.array([o.bi_ratio for o in occs])[occ_idx] * _mean_one_lognormal(rng, 0.3, n),
        -2,
    )
    tiv = building + contents + bi

    # ---- accounts
    personal = np.array([o.personal_lines for o in occs])[occ_idx]
    acc_id = np.empty(n, dtype=np.int64)
    p_idx = np.flatnonzero(personal)
    acc_id[p_idx] = np.arange(p_idx.size)
    c_idx = rng.permutation(np.flatnonzero(~personal))
    sizes = _commercial_account_sizes(
        rng, c_idx.size, cfg.accounts.commercial_mean_locations, cfg.accounts.size_tail_alpha
    )
    acc_id[c_idx] = p_idx.size + np.repeat(np.arange(sizes.size), sizes)
    n_acc = p_idx.size + sizes.size
    is_commercial_acc = np.arange(n_acc) >= p_idx.size

    # ---- location table
    hub_perils = np.array([";".join(h.perils) for h in hubs], dtype=object)
    hub_names = np.array([h.name for h in hubs], dtype=object)
    loc_numbers = np.char.add("L", np.char.zfill(np.arange(1, n + 1).astype(str), 8))
    acc_numbers_all = np.char.add("A", np.char.zfill(np.arange(1, n_acc + 1).astype(str), 8))
    acc_numbers = acc_numbers_all[acc_id]
    ded_pct = np.array([h.ded_pct_tiv for h in hubs])[hub_idx]
    countries = np.array([h.country for h in hubs], dtype=object)[hub_idx]

    loc_cols: dict[str, pa.Array] = {
        "PortNumber": pa.array(np.full(n, cfg.portfolio_number, dtype=object), pa.string()),
        "AccNumber": pa.array(acc_numbers, pa.string()),
        "LocNumber": pa.array(loc_numbers, pa.string()),
        "LocName": pa.array(hub_names[hub_idx], pa.string()),
        "CountryCode": pa.array(countries, pa.string()),
        "Latitude": pa.array(np.round(lat, 6)),
        "Longitude": pa.array(np.round(lon, 6)),
        "OccupancyCode": pa.array(np.array([o.code for o in occs])[occ_idx], pa.int32()),
        "ConstructionCode": pa.array(construction, pa.int32()),
        "YearBuilt": pa.array(year_built, pa.int32()),
        "NumberOfStoreys": pa.array(storeys, pa.int32()),
        "LocPerilsCovered": pa.array(hub_perils[hub_idx], pa.string()),
        "BuildingTIV": pa.array(building),
        "OtherTIV": pa.array(np.zeros(n)),
        "ContentsTIV": pa.array(contents),
        "BITIV": pa.array(bi),
        "LocCurrency": pa.array(np.full(n, cfg.currency, dtype=object), pa.string()),
        "LocDed6All": pa.array(ded_pct),
        "LocDedType6All": pa.array(np.full(n, 2), pa.int32()),
        "LocLimit6All": pa.array(np.zeros(n)),
        "FlexiLocHub": pa.array(hub_names[hub_idx], pa.string()),
    }
    locations = pa.table(loc_cols, schema=oed.LOCATION_SCHEMA)
    derived = {
        f"{oed.DERIVED_PREFIX}tiv": pa.array(tiv),
        f"{oed.DERIVED_PREFIX}dist_km": pa.array(np.round(r_km, 3)),
        **{
            f"{oed.DERIVED_PREFIX}h3_r{res}": pa.array(h3_cells(lat, lon, res), pa.uint64())
            for res in cfg.h3_resolutions
        },
    }
    for name, arr in derived.items():
        locations = locations.append_column(name, arr)

    # ---- account / policy table (one policy, one layer per account)
    acc_tiv = np.bincount(acc_id, weights=tiv, minlength=n_acc)
    peril_bits = np.zeros((n_acc, len(oed.PERILS)), dtype=bool)
    peril_codes = list(oed.PERILS)
    for j, code in enumerate(peril_codes):
        has = np.array([code in h.perils for h in hubs])[hub_idx]
        peril_bits[:, j] = np.bincount(acc_id, weights=has, minlength=n_acc) > 0
    acc_perils = np.array(
        [";".join(c for c, b in zip(peril_codes, row, strict=True) if b) for row in peril_bits],
        dtype=object,
    )

    a = cfg.accounts
    participation = np.where(
        is_commercial_acc, np.round(rng.uniform(*a.participation, n_acc), 2), 1.0
    )
    limit = np.where(
        is_commercial_acc, np.round(acc_tiv * rng.uniform(*a.limit_ratio, n_acc), -3), acc_tiv
    )
    attachment = np.where(
        is_commercial_acc, np.round(acc_tiv * rng.uniform(*a.attachment_ratio, n_acc), -3), 0.0
    )
    pol_ded = np.where(is_commercial_acc, np.round(acc_tiv * 0.001, -3), 0.0)

    accounts = pa.table(
        {
            "PortNumber": pa.array(np.full(n_acc, cfg.portfolio_number, dtype=object), pa.string()),
            "AccNumber": pa.array(acc_numbers_all, pa.string()),
            "AccCurrency": pa.array(np.full(n_acc, cfg.currency, dtype=object), pa.string()),
            "PolNumber": pa.array(
                np.char.replace(acc_numbers_all, "A", "POL", count=1), pa.string()
            ),
            "PolPerilsCovered": pa.array(acc_perils, pa.string()),
            "PolPeril": pa.array(acc_perils, pa.string()),
            "LayerNumber": pa.array(np.ones(n_acc), pa.int32()),
            "LayerParticipation": pa.array(participation),
            "LayerLimit": pa.array(limit),
            "LayerAttachment": pa.array(attachment),
            "PolDed6All": pa.array(pol_ded),
            "PolDedType6All": pa.array(np.zeros(n_acc), pa.int32()),
        },
        schema=oed.ACCOUNT_SCHEMA,
    )
    return Portfolio(config=cfg, locations=locations, accounts=accounts)
