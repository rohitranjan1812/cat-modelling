"""Plugin registries.

Third-party packages extend catmod without forking it by declaring entry points:

    [project.entry-points."catmod.spatial_fields"]
    matern = "my_pkg.fields:MaternField"

    [project.entry-points."catmod.copulas"]
    t_copula = "my_pkg.copulas:StudentTCopula"

    [project.entry-points."catmod.hazards"]
    flood = "my_pkg.flood:FloodHazard"

A plugin class must expose a ``name`` class attribute and satisfy the matching Protocol
(``SpatialField``, ``DamageCopula`` or ``Hazard``). Built-ins are registered first; plugins with
the same name override them deliberately.
"""

from __future__ import annotations

from functools import cache
from importlib.metadata import entry_points
from typing import Any


def _load(group: str, builtins: dict[str, Any]) -> dict[str, Any]:
    out = dict(builtins)
    for ep in entry_points(group=group):
        out[ep.name] = ep.load()
    return out


@cache
def spatial_fields() -> dict[str, Any]:
    from catmod.engine.fields import FIELDS

    return _load("catmod.spatial_fields", dict(FIELDS))


@cache
def copulas() -> dict[str, Any]:
    from catmod.engine.copulas import IndependentCopula, OneFactorCopula, SpatialCopula

    return _load(
        "catmod.copulas",
        {c.name: c for c in (IndependentCopula, OneFactorCopula, SpatialCopula)},
    )


@cache
def hazards() -> dict[str, Any]:
    from catmod.engine.eq import EarthquakeHazard
    from catmod.engine.tc import CycloneHazard

    return _load("catmod.hazards", {"QEQ": EarthquakeHazard, "WTC": CycloneHazard})


def describe() -> dict[str, list[str]]:
    return {
        "spatial_fields": sorted(spatial_fields()),
        "copulas": sorted(copulas()),
        "hazards": sorted(hazards()),
    }
