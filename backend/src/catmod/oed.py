"""Open Exposure Data (OED) vocabulary and column layouts.

Only the subset of OED that catmod currently reads or writes is declared here. Codes follow
the OED code ranges: occupancy 1050-1099 residential, 1100-1149 commercial, 1150-1199
industrial; construction 5000 unknown, 5050 wood, 5100 masonry, 5150 reinforced concrete,
5200 steel. Peril codes are OED peril codes.
"""

from __future__ import annotations

from typing import Final

import pyarrow as pa

PERILS: Final[dict[str, str]] = {
    "QEQ": "Earthquake - shake",
    "WTC": "Tropical cyclone - wind",
}

OCCUPANCY: Final[dict[int, str]] = {
    1050: "Residential (general)",
    1051: "Single family dwelling",
    1052: "Multi-family dwelling",
    1100: "Commercial (general)",
    1150: "Industrial (general)",
}

CONSTRUCTION: Final[dict[int, str]] = {
    5000: "Unknown",
    5050: "Wood",
    5100: "Masonry",
    5150: "Reinforced concrete",
    5200: "Steel",
}

DED_TYPE: Final[dict[int, str]] = {
    0: "Monetary amount",
    1: "% of loss",
    2: "% of TIV",
}

# OED location file columns emitted by catmod (order is the CSV column order).
LOCATION_FIELDS: Final[list[pa.Field]] = [
    pa.field("PortNumber", pa.string(), nullable=False),
    pa.field("AccNumber", pa.string(), nullable=False),
    pa.field("LocNumber", pa.string(), nullable=False),
    pa.field("LocName", pa.string()),
    pa.field("CountryCode", pa.string(), nullable=False),
    pa.field("Latitude", pa.float64(), nullable=False),
    pa.field("Longitude", pa.float64(), nullable=False),
    pa.field("OccupancyCode", pa.int32(), nullable=False),
    pa.field("ConstructionCode", pa.int32(), nullable=False),
    pa.field("YearBuilt", pa.int32()),
    pa.field("NumberOfStoreys", pa.int32()),
    pa.field("LocPerilsCovered", pa.string(), nullable=False),
    pa.field("BuildingTIV", pa.float64(), nullable=False),
    pa.field("OtherTIV", pa.float64(), nullable=False),
    pa.field("ContentsTIV", pa.float64(), nullable=False),
    pa.field("BITIV", pa.float64(), nullable=False),
    pa.field("LocCurrency", pa.string(), nullable=False),
    pa.field("LocDed6All", pa.float64()),
    pa.field("LocDedType6All", pa.int32()),
    pa.field("LocLimit6All", pa.float64()),
    pa.field("FlexiLocHub", pa.string()),
]

# Derived analytic columns kept in Parquet only (never in OED CSV).
DERIVED_PREFIX: Final[str] = "catmod_"

ACCOUNT_FIELDS: Final[list[pa.Field]] = [
    pa.field("PortNumber", pa.string(), nullable=False),
    pa.field("AccNumber", pa.string(), nullable=False),
    pa.field("AccCurrency", pa.string(), nullable=False),
    pa.field("PolNumber", pa.string(), nullable=False),
    pa.field("PolPerilsCovered", pa.string(), nullable=False),
    pa.field("PolPeril", pa.string(), nullable=False),
    pa.field("LayerNumber", pa.int32(), nullable=False),
    pa.field("LayerParticipation", pa.float64(), nullable=False),
    pa.field("LayerLimit", pa.float64(), nullable=False),
    pa.field("LayerAttachment", pa.float64(), nullable=False),
    pa.field("PolDed6All", pa.float64()),
    pa.field("PolDedType6All", pa.int32()),
]

LOCATION_SCHEMA: Final[pa.Schema] = pa.schema(LOCATION_FIELDS)
ACCOUNT_SCHEMA: Final[pa.Schema] = pa.schema(ACCOUNT_FIELDS)
