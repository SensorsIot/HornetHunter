"""Wire format between the Kraken Pis and the Management Pi.

Both sides import this module, so a change here is a change to the contract:
bump SCHEMA_VERSION when the payload shape changes incompatibly.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BearingReport:
    """One direction-of-arrival measurement from one station."""

    station_id: str
    timestamp: float
    latitude: float
    longitude: float
    bearing_deg: float
    confidence: float
    frequency_hz: int
    schema_version: int = SCHEMA_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> BearingReport:
        data: Any = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError(f"expected a JSON object, got {type(data).__name__}")

        version = data.get("schema_version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {version!r} (expected {SCHEMA_VERSION})")

        missing = {f for f in _REQUIRED_FIELDS if f not in data}
        if missing:
            raise ValueError(f"missing field(s): {', '.join(sorted(missing))}")

        return cls(
            station_id=str(data["station_id"]),
            timestamp=float(data["timestamp"]),
            latitude=float(data["latitude"]),
            longitude=float(data["longitude"]),
            bearing_deg=float(data["bearing_deg"]),
            confidence=float(data["confidence"]),
            frequency_hz=int(data["frequency_hz"]),
        )


_REQUIRED_FIELDS = frozenset(
    {
        "station_id",
        "timestamp",
        "latitude",
        "longitude",
        "bearing_deg",
        "confidence",
        "frequency_hz",
    }
)
