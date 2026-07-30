"""Bearing pipeline (FSD §9).

Retains the most recent `Measurement` (FR-9.1) and, on demand, renders the compact
`BearingReport` transmitted once per cycle (FR-9.2). Every record carries the
measurement's age in milliseconds at the moment of transmission (FR-9.3); ages past
`max_age_ms`, and any age that would overflow the `u16` field, are reported with
`no_data` set (§9.7). Bearings outside 0..359.99° are discarded and counted (§9.7).

Position rides only on change: it is transmitted only when the station has moved
beyond `position_epsilon_dm` decimetres from the last transmitted position (FR-9.5),
against a per-station reference.

The monotonic clock is injected so the pipeline is host-testable with no real time.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from hornethunter_shared.bearing import AGE_MAX
from hornethunter_shared.geo import LatLon, distance_m
from hornethunter_shared.messages import (
    FLAG_ADC_OVERDRIVE,
    FLAG_KRAKEN_LINK_UP,
    FLAG_NO_DATA,
    FLAG_POSITION_PRESENT,
    FLAG_SQUELCH_OPEN,
    BearingReport,
)

from .measurement import Measurement


class BearingPipeline:
    """Holds the latest measurement and builds the poll response (§9)."""

    def __init__(
        self,
        *,
        station_id: str,
        reference: LatLon | None = None,
        position_epsilon_dm: int = 50,
        max_age_ms: int = 5000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._station_id = station_id
        self._reference = reference
        self._epsilon_dm = position_epsilon_dm
        self._max_age_ms = max_age_ms
        self._clock = clock
        self._latest: Measurement | None = None
        self._last_sent: LatLon | None = None
        # Counters since the previous poll (FR-9.4).
        self.produced = 0
        self.discarded = 0

    def update(self, measurement: Measurement) -> bool:
        """Accept a new measurement. Out-of-range bearings are discarded and counted
        (§9.7); the previous measurement is retained. Returns True when accepted."""
        if not (0.0 <= measurement.bearing_deg < 360.0):
            self.discarded += 1
            return False
        self._latest = measurement
        self.produced += 1
        return True

    def reset_counts(self) -> None:
        """Clear the produced/discarded counters after a poll has reported them."""
        self.produced = 0
        self.discarded = 0

    def build(
        self,
        *,
        config_version: int,
        config_crc: int,
        link_up: bool = True,
        now: float | None = None,
    ) -> BearingReport:
        """Render the current state as a `BearingReport` (FR-9.2, FR-9.3, FR-9.6)."""
        now = self._clock() if now is None else now
        measurement = self._latest
        if measurement is None:
            return self._no_data(config_version, config_crc)

        raw_age_ms = int(round((now - measurement.mono_ts) * 1000))
        if raw_age_ms < 0:
            raw_age_ms = 0
        overflow = raw_age_ms > AGE_MAX
        age_ms = AGE_MAX if overflow else raw_age_ms
        stale = overflow or raw_age_ms > self._max_age_ms

        flags = 0
        latitude: float | None = None
        longitude: float | None = None
        if stale:
            flags |= FLAG_NO_DATA
        else:
            if link_up:
                flags |= FLAG_KRAKEN_LINK_UP
            if measurement.adc_overdrive:
                flags |= FLAG_ADC_OVERDRIVE
            if measurement.squelch_open:
                flags |= FLAG_SQUELCH_OPEN
            latitude, longitude = self._position_to_send(measurement)
            if latitude is not None:
                flags |= FLAG_POSITION_PRESENT

        return BearingReport(
            station_id=self._station_id,
            age_ms=age_ms,
            bearing_deg=measurement.bearing_deg,
            confidence=measurement.confidence,
            power_dbm=measurement.power_dbm,
            config_version=config_version,
            config_crc=config_crc,
            flags=flags,
            latitude=latitude,
            longitude=longitude,
        )

    def _no_data(self, config_version: int, config_crc: int) -> BearingReport:
        return BearingReport(
            station_id=self._station_id,
            age_ms=0,
            bearing_deg=0.0,
            confidence=0.0,
            power_dbm=0.0,
            config_version=config_version,
            config_crc=config_crc,
            flags=FLAG_NO_DATA,
        )

    def _position_to_send(self, measurement: Measurement) -> tuple[float | None, float | None]:
        """Return position only when it has moved beyond the epsilon (FR-9.5)."""
        if self._reference is None or measurement.latitude is None or measurement.longitude is None:
            return None, None
        here = LatLon(measurement.latitude, measurement.longitude)
        if self._last_sent is not None:
            moved_dm = distance_m(self._last_sent, here) * 10.0
            if moved_dm < self._epsilon_dm:
                return None, None
        self._last_sent = here
        return measurement.latitude, measurement.longitude
