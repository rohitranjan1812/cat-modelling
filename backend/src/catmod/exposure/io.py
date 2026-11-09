"""Persist portfolios: enriched Parquet (analytics) + strict OED CSV (interchange)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import duckdb
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
from pydantic import BaseModel

from catmod import oed
from catmod.exposure.synthetic import Portfolio

Format = Literal["parquet", "csv"]
LOCATION_PARQUET = "location.parquet"
ACCOUNT_PARQUET = "account.parquet"


class PerilExposure(BaseModel):
    peril: str
    n_locations: int
    tiv: float


class PortfolioSummary(BaseModel):
    n_locations: int
    n_accounts: int
    tiv_total: float
    tiv_building: float
    tiv_contents: float
    tiv_bi: float
    by_peril: list[PerilExposure]
    bbox: tuple[float, float, float, float]  # min_lon, min_lat, max_lon, max_lat


def write_portfolio(
    p: Portfolio, out_dir: Path, formats: tuple[Format, ...] = ("parquet",)
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if "parquet" in formats:
        for table, name in ((p.locations, LOCATION_PARQUET), (p.accounts, ACCOUNT_PARQUET)):
            path = out_dir / name
            pq.write_table(table, path, compression="zstd", row_group_size=128_000)
            written.append(path)
    if "csv" in formats:
        oed_loc = p.locations.select([f.name for f in oed.LOCATION_FIELDS])
        for table, name in ((oed_loc, "location.csv"), (p.accounts, "account.csv")):
            path = out_dir / name
            pacsv.write_csv(table, path)
            written.append(path)
    return written


def summarize(locations: pa.Table, accounts: pa.Table) -> PortfolioSummary:
    con = duckdb.connect()
    con.register("loc", locations)
    row = con.execute(
        """SELECT count(*), sum(catmod_tiv), sum(BuildingTIV), sum(ContentsTIV), sum(BITIV),
                  min(Longitude), min(Latitude), max(Longitude), max(Latitude)
           FROM loc"""
    ).fetchone()
    assert row is not None
    perils = con.execute(
        """SELECT peril, count(*), sum(catmod_tiv)
           FROM (SELECT unnest(string_split(LocPerilsCovered, ';')) AS peril, catmod_tiv FROM loc)
           GROUP BY peril ORDER BY peril"""
    ).fetchall()
    return PortfolioSummary(
        n_locations=int(row[0]),
        n_accounts=accounts.num_rows,
        tiv_total=float(row[1]),
        tiv_building=float(row[2]),
        tiv_contents=float(row[3]),
        tiv_bi=float(row[4]),
        by_peril=[PerilExposure(peril=p, n_locations=int(c), tiv=float(t)) for p, c, t in perils],
        bbox=(float(row[5]), float(row[6]), float(row[7]), float(row[8])),
    )
