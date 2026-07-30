"""Wire format between the Kraken Pis and the Management Pi.

Both sides import this module, so a change here is a change to the contract:
bump SCHEMA_VERSION when the payload shape changes incompatibly.

The record shape is specified in `docs/hornethunter-fsd.md` §9.3. Two properties
are worth stating here because they are easy to get wrong:

* There is **no absolute timestamp**. v1 produces no position fix, so no
  cross-station time alignment is required; a report carries its `age_ms` at the
  moment of transmission and the receiver converts to absolute time using its own
  clock (FSD §9.5).
* Position is **optional**. It is transmitted only when the station has moved
  (FSD FR-9.5), so `latitude`/`longitude` are absent on most reports.

This module currently provides the JSON form only. The compact binary codec used
on the LoRa carrier is Phase 1 work (FSD §3.1); JSON remains the form used in
logs and tests, and is never the wire form (FSD §10.1).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

SCHEMA_VERSION = 2

# Report flags (FSD §9.3).
FLAG_POSITION_PRESENT = 1 << 0
FLAG_POSITION_FROM_GPS = 1 << 1
FLAG_KRAKEN_LINK_UP = 1 << 2
FLAG_SQUELCH_OPEN = 1 << 3
FLAG_ADC_OVERDRIVE = 1 << 4
FLAG_NO_DATA = 1 << 5


@dataclass(frozen=True)
class BearingReport:
    """One direction-of-arrival measurement from one station."""

    station_id: str
    age_ms: int
    bearing_deg: float
    confidence: float
    power_dbm: float
    config_version: int
    config_crc: int
    flags: int = 0
    latitude: float | None = None
    longitude: float | None = None
    schema_version: int = SCHEMA_VERSION

    @property
    def has_position(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def has_data(self) -> bool:
        """False when the station had no usable measurement to report."""
        return not self.flags & FLAG_NO_DATA

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

        latitude = data.get("latitude")
        longitude = data.get("longitude")

        return cls(
            station_id=str(data["station_id"]),
            age_ms=int(data["age_ms"]),
            bearing_deg=float(data["bearing_deg"]),
            confidence=float(data["confidence"]),
            power_dbm=float(data["power_dbm"]),
            config_version=int(data["config_version"]),
            config_crc=int(data["config_crc"]),
            flags=int(data.get("flags", 0)),
            latitude=None if latitude is None else float(latitude),
            longitude=None if longitude is None else float(longitude),
        )


# latitude/longitude are deliberately absent: position rides only on change.
_REQUIRED_FIELDS = frozenset(
    {
        "station_id",
        "age_ms",
        "bearing_deg",
        "confidence",
        "power_dbm",
        "config_version",
        "config_crc",
    }
)
