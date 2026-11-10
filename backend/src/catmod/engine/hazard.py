"""Common hazard abstractions shared by all perils."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

import numpy as np
import numpy.typing as npt

from catmod.engine.context import Exposure

F64 = npt.NDArray[np.float64]
I64 = npt.NDArray[np.int64]


@dataclass(frozen=True)
class Event:
    event_id: int
    peril: str
    rate: float
    name: str

    def summary(self) -> dict[str, Any]:
        """Flat columns for the event table (lat/lon = representative point)."""
        raise NotImplementedError

    def geometry(self) -> dict[str, Any]:
        """GeoJSON geometry (rupture line / storm track) for maps."""
        raise NotImplementedError


@dataclass(frozen=True)
class Footprint:
    """Median log-intensity at affected sites plus residual structure."""

    idx: I64  # exposure indices
    ln_median: F64  # ln of median intensity
    tau: float  # between-event sigma (ln units)
    phi: float  # within-event sigma (ln units)
    x_km: F64  # local projection for spatial fields
    y_km: F64


class Hazard(Protocol):
    peril: ClassVar[str]
    unit: ClassVar[str]

    def catalog(self) -> list[Event]: ...

    def footprint(self, event: Event, exposure: Exposure) -> Footprint: ...

    def event_from_spec(self, spec: dict[str, Any], event_id: int) -> Event: ...

    def field_params(self) -> dict[str, Any]: ...


def empty_footprint(tau: float, phi: float) -> Footprint:
    z = np.zeros(0)
    return Footprint(np.zeros(0, dtype=np.int64), z, tau, phi, z, z)
