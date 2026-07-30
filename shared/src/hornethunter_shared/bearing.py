"""Binary codec for the compact bearing record (FSD §9.3).

The record is 10 bytes, or 14 with position. Position, when present, is carried as
a signed decimetre offset (north, east) from a per-station reference (FSD §9.4);
the ±32767 dm range spans ±3.2 km, beyond which a reference re-base is required
(FSD §9.7). station_id is not in the record — the receiver identifies the station
from the frame's source address.

    flags u8 · bearing_cdeg u16 · confidence u8 · power_dbm i8 · age_ms u16 ·
    config_version u8 · config_crc u16   [ · dlat i16 · dlon i16 ]
"""

from __future__ import annotations

import struct

from .geo import LatLon, from_local_enu, normalize_bearing, to_local_enu
from .messages import FLAG_POSITION_PRESENT, BearingReport

_FIXED = struct.Struct(">BHBbHBH")  # 10 bytes
_POSITION = struct.Struct(">hh")  # 4 bytes
AGE_MAX = 0xFFFF
_POS_LIMIT_DM = 32767


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def encode_bearing(report: BearingReport, reference: LatLon | None = None) -> bytes:
    """Encode a report to its wire record.

    Position is included only when the report carries one and a `reference` is
    given (FR-9.5). The stored `flags` reflect whether position is actually present.
    """
    include_position = report.has_position and reference is not None
    flags = report.flags & ~FLAG_POSITION_PRESENT
    if include_position:
        flags |= FLAG_POSITION_PRESENT

    cdeg = round(normalize_bearing(report.bearing_deg) * 100) % 36000
    fixed = _FIXED.pack(
        flags & 0xFF,
        cdeg,
        _clamp(round(report.confidence), 0, 255),
        _clamp(round(report.power_dbm), -128, 127),
        _clamp(report.age_ms, 0, AGE_MAX),
        report.config_version & 0xFF,
        report.config_crc & 0xFFFF,
    )
    if not include_position:
        return fixed

    assert reference is not None and report.latitude is not None
    east_m, north_m = to_local_enu(reference, LatLon(report.latitude, report.longitude))  # type: ignore[arg-type]
    dlat = _clamp(round(north_m * 10), -_POS_LIMIT_DM, _POS_LIMIT_DM)
    dlon = _clamp(round(east_m * 10), -_POS_LIMIT_DM, _POS_LIMIT_DM)
    return fixed + _POSITION.pack(dlat, dlon)


def decode_bearing(
    data: bytes, station_id: str, reference: LatLon | None = None
) -> BearingReport:
    """Decode a wire record into a report, attributing it to `station_id`.

    When the record carries position and a `reference` is supplied, latitude and
    longitude are reconstructed; without a reference the offsets are dropped.
    """
    if len(data) < _FIXED.size:
        raise ValueError(f"bearing record too short: {len(data)} B")
    flags, cdeg, conf, power, age_ms, version, crc = _FIXED.unpack(data[: _FIXED.size])

    latitude: float | None = None
    longitude: float | None = None
    if flags & FLAG_POSITION_PRESENT:
        rest = data[_FIXED.size :]
        if len(rest) < _POSITION.size:
            raise ValueError("position flag set but record has no position bytes")
        dlat, dlon = _POSITION.unpack(rest[: _POSITION.size])
        if reference is not None:
            here = from_local_enu(reference, dlon / 10.0, dlat / 10.0)
            latitude, longitude = here.lat, here.lon

    return BearingReport(
        station_id=station_id,
        age_ms=age_ms,
        bearing_deg=cdeg / 100.0,
        confidence=float(conf),
        power_dbm=float(power),
        config_version=version,
        config_crc=crc,
        flags=flags,
        latitude=latitude,
        longitude=longitude,
    )
