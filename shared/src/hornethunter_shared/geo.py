"""Geodesy and bearing intersection.

Positions are WGS84 degrees. Bearings are degrees clockwise from true north.

Distances use a local east/north tangent plane about a reference point, which is
accurate to well under a metre over the few kilometres a hornet-tracking net
covers. It is not suitable for cross-continent baselines.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class LatLon:
    lat: float
    lon: float


def normalize_bearing(bearing_deg: float) -> float:
    """Wrap a bearing into [0, 360)."""
    return bearing_deg % 360.0


def to_local_enu(origin: LatLon, point: LatLon) -> tuple[float, float]:
    """Project `point` to metres east/north of `origin`."""
    east = (
        math.radians(point.lon - origin.lon) * EARTH_RADIUS_M * math.cos(math.radians(origin.lat))
    )
    north = math.radians(point.lat - origin.lat) * EARTH_RADIUS_M
    return east, north


def from_local_enu(origin: LatLon, east_m: float, north_m: float) -> LatLon:
    """Inverse of :func:`to_local_enu`."""
    lat = origin.lat + math.degrees(north_m / EARTH_RADIUS_M)
    lon = origin.lon + math.degrees(
        east_m / (EARTH_RADIUS_M * math.cos(math.radians(origin.lat)))
    )
    return LatLon(lat, lon)


def distance_m(a: LatLon, b: LatLon) -> float:
    """Great-circle distance in metres (haversine)."""
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def initial_bearing_deg(a: LatLon, b: LatLon) -> float:
    """Initial great-circle bearing from `a` to `b`."""
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlon = math.radians(b.lon - a.lon)
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return normalize_bearing(math.degrees(math.atan2(y, x)))


def intersect_bearings(
    station_a: LatLon,
    bearing_a_deg: float,
    station_b: LatLon,
    bearing_b_deg: float,
) -> LatLon | None:
    """Intersect two bearing lines.

    Returns the fix, or None when the lines are parallel or when the crossing
    lies behind either station (both rays must point at the target).
    """
    origin = station_a
    ax, ay = to_local_enu(origin, station_a)
    bx, by = to_local_enu(origin, station_b)

    a_rad = math.radians(normalize_bearing(bearing_a_deg))
    b_rad = math.radians(normalize_bearing(bearing_b_deg))
    dae, dan = math.sin(a_rad), math.cos(a_rad)
    dbe, dbn = math.sin(b_rad), math.cos(b_rad)

    det = dbe * dan - dae * dbn
    if abs(det) < 1e-12:
        return None

    dx, dy = bx - ax, by - ay
    t_a = (-dx * dbn + dbe * dy) / det
    t_b = (dae * dy - dan * dx) / det
    if t_a <= 0 or t_b <= 0:
        return None

    return from_local_enu(origin, ax + t_a * dae, ay + t_a * dan)


def triangulate(observations: list[tuple[LatLon, float]]) -> LatLon | None:
    """Estimate a transmitter position from station/bearing pairs.

    Every pair of observations that crosses in front of both stations yields a
    fix; the result is the mean of those fixes. Returns None if no pair crosses.
    """
    fixes: list[LatLon] = []
    for i in range(len(observations)):
        for j in range(i + 1, len(observations)):
            (station_a, bearing_a), (station_b, bearing_b) = observations[i], observations[j]
            fix = intersect_bearings(station_a, bearing_a, station_b, bearing_b)
            if fix is not None:
                fixes.append(fix)

    if not fixes:
        return None
    return LatLon(
        sum(f.lat for f in fixes) / len(fixes),
        sum(f.lon for f in fixes) / len(fixes),
    )
