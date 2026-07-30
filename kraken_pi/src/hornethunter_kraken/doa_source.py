"""Kraken DoA source interface (FSD §12).

Three backends present **one** internal `Measurement` type; no caller branches on
backend (FR-12.3). The simulator and the real KrakenSDR use different field names
and transports, so a pure, table-driven adapter maps each raw record to a
`Measurement` (NFR-12.1); the mapping is tested with plain dicts, no network.

* `SyntheticSource` — deterministic in-process generator, no I/O (host tier).
* `SimulatorSource` — HTTP `GET /api/v1/doa` (the `KrakenSimulator`).
* `KrakenSource` — WebSocket push on `ws://127.0.0.1:8021` (real hardware).

Network sources reconnect with capped exponential backoff (FR-12.1), expose feed
availability as explicit state (FR-12.2), and discard-and-count malformed or
missing-field records (FR-12.4).
"""

from __future__ import annotations

import abc
import contextlib
import json
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .measurement import Measurement


class AdapterError(ValueError):
    """A raw DoA record was missing a required field or had an unusable type."""


@dataclass(frozen=True)
class MeasurementAdapter:
    """Pure, table-driven map from a raw record to a `Measurement` (§12.3).

    The four required fields differ in name between the simulator and the real
    software; the rest share names and are optional. Mapping is a pure function of
    the record, testable without a network (NFR-12.1).
    """

    bearing: str
    quality: str
    power: str
    frequency: str
    latitude: str = "latitude"
    longitude: str = "longitude"
    adc_overdrive: str = "adc_overdrive"
    squelch_open: str = "squelch_open"
    num_corr_sources: str = "num_corr_sources"
    snr: str = "snr"

    def adapt(self, record: Mapping[str, Any], *, mono_ts: float) -> Measurement:
        return Measurement(
            bearing_deg=_require_float(record, self.bearing),
            confidence=_require_float(record, self.quality),
            power_dbm=_require_float(record, self.power),
            freq_hz=_require_float(record, self.frequency),
            latitude=_optional_float(record, self.latitude),
            longitude=_optional_float(record, self.longitude),
            adc_overdrive=bool(record.get(self.adc_overdrive, False)),
            squelch_open=bool(record.get(self.squelch_open, False)),
            num_corr_sources=_optional_int(record, self.num_corr_sources),
            snr=_optional_float(record, self.snr) or 0.0,
            mono_ts=mono_ts,
        )


# §12.3 adapter table. Real: radioBearing/conf/power/freq. Simulator field names differ.
KRAKEN_ADAPTER = MeasurementAdapter(
    bearing="radioBearing", quality="conf", power="power", frequency="freq"
)
SIMULATOR_ADAPTER = MeasurementAdapter(
    bearing="bearing_deg", quality="width_rad", power="rssi_dbfs", frequency="center_freq_hz"
)


def _require_float(record: Mapping[str, Any], key: str) -> float:
    if key not in record:
        raise AdapterError(f"missing required field {key!r}")
    try:
        return float(record[key])
    except (TypeError, ValueError) as exc:
        raise AdapterError(f"field {key!r} is not numeric: {record[key]!r}") from exc


def _optional_float(record: Mapping[str, Any], key: str) -> float | None:
    value = record.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(record: Mapping[str, Any], key: str) -> int:
    value = record.get(key)
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_json_object(raw: str | bytes) -> dict[str, Any]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise AdapterError(f"expected a JSON object, got {type(data).__name__}")
    return data


@dataclass
class Backoff:
    """Capped exponential backoff for reconnection (FR-12.1)."""

    base_s: float = 0.5
    cap_s: float = 30.0
    _current: float = 0.0

    def reset(self) -> None:
        self._current = 0.0

    def next_delay(self) -> float:
        self._current = self.base_s if self._current == 0.0 else min(self._current * 2, self.cap_s)
        return self._current


class DoaSource(abc.ABC):
    """Common interface for every DoA backend (FR-12.3).

    `latest()` returns the most recent measurement or None; `available` is the
    explicit feed state reported in every record (FR-12.2). `pump()` advances the
    source — generating, polling, or reading a pushed frame — and must never raise
    on an I/O fault, so a dead feed cannot stall the poll cycle (§2.3).
    """

    def __init__(self) -> None:
        self.available: bool = False
        self.produced: int = 0
        self.discarded: int = 0
        self._latest: Measurement | None = None

    def latest(self) -> Measurement | None:
        return self._latest

    @abc.abstractmethod
    def pump(self) -> None: ...

    def close(self) -> None:
        return None


class SyntheticSource(DoaSource):
    """Deterministic in-process generator for host-tier tests (no I/O)."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> None:
        super().__init__()
        self._clock = clock
        self._latitude = latitude
        self._longitude = longitude
        self._n = 0
        self.available = True

    def pump(self) -> None:
        self._n += 1
        n = self._n
        self._latest = Measurement(
            bearing_deg=float((n * 30) % 360),
            confidence=float(100 + (n % 6) * 10),  # >100 occurs; carried as-is (§9.3)
            power_dbm=float(-40 - (n % 7)),
            freq_hz=148_524_000.0,
            latitude=self._latitude,
            longitude=self._longitude,
            adc_overdrive=(n % 11 == 0),
            squelch_open=(n % 2 == 0),
            num_corr_sources=4,
            snr=float(10 + (n % 3)),
            mono_ts=self._clock(),
        )
        self.produced += 1


class SimulatorSource(DoaSource):
    """`KrakenSimulator` backend over HTTP `GET /api/v1/doa` (§12.3)."""

    def __init__(
        self,
        url: str,
        *,
        adapter: MeasurementAdapter = SIMULATOR_ADAPTER,
        clock: Callable[[], float] = time.monotonic,
        opener: Callable[[str], Any] | None = None,
        timeout_s: float = 1.0,
    ) -> None:
        super().__init__()
        self._url = url
        self._adapter = adapter
        self._clock = clock
        self._opener = opener or (lambda u: urllib.request.urlopen(u, timeout=timeout_s))
        self._backoff = Backoff()
        self._next_attempt = 0.0

    def pump(self) -> None:
        now = self._clock()
        if now < self._next_attempt:
            return
        try:
            with self._opener(self._url) as response:
                raw = response.read()
        except OSError:
            self.available = False
            self._next_attempt = now + self._backoff.next_delay()
            return
        self.available = True
        self._backoff.reset()
        self._ingest(raw)

    def _ingest(self, raw: str | bytes) -> None:
        try:
            record = _parse_json_object(raw)
            measurement = self._adapter.adapt(record, mono_ts=self._clock())
        except AdapterError:
            self.discarded += 1
            return
        self._latest = measurement
        self.produced += 1


class KrakenSource(DoaSource):
    """Real KrakenSDR backend over the WebSocket push feed (§12.2, §12.3)."""

    def __init__(
        self,
        url: str = "ws://127.0.0.1:8021",
        *,
        adapter: MeasurementAdapter = KRAKEN_ADAPTER,
        clock: Callable[[], float] = time.monotonic,
        connector: Callable[[str], Any] | None = None,
    ) -> None:
        super().__init__()
        self._url = url
        self._adapter = adapter
        self._clock = clock
        self._connector = connector
        self._backoff = Backoff()
        self._next_attempt = 0.0
        self._ws: Any = None

    def _connect(self) -> Any:
        if self._connector is not None:
            return self._connector(self._url)
        import websocket  # lazy: real hardware only

        return websocket.create_connection(self._url, timeout=1.0)

    def pump(self) -> None:
        now = self._clock()
        if self._ws is None:
            if now < self._next_attempt:
                return
            try:
                self._ws = self._connect()
            except OSError:
                self.available = False
                self._next_attempt = now + self._backoff.next_delay()
                return
            self.available = True
            self._backoff.reset()
        try:
            raw = self._ws.recv()
        except OSError:
            self._drop(now)
            return
        self._ingest(raw)

    def _drop(self, now: float) -> None:
        self._ws = None
        self.available = False
        self._next_attempt = now + self._backoff.next_delay()

    def _ingest(self, raw: str | bytes) -> None:
        try:
            record = _parse_json_object(raw)
            measurement = self._adapter.adapt(record, mono_ts=self._clock())
        except AdapterError:
            self.discarded += 1
            return
        self._latest = measurement
        self.produced += 1

    def close(self) -> None:
        if self._ws is not None:
            with contextlib.suppress(OSError):
                self._ws.close()
            self._ws = None


def build_source(
    backend: str,
    *,
    ws_url: str = "ws://127.0.0.1:8021",
    sim_url: str = "http://127.0.0.1:8080/api/v1/doa",
    clock: Callable[[], float] = time.monotonic,
    latitude: float | None = None,
    longitude: float | None = None,
) -> DoaSource:
    """Construct the configured backend (§12.3). All present one internal type."""
    if backend == "synthetic":
        return SyntheticSource(clock=clock, latitude=latitude, longitude=longitude)
    if backend == "simulator":
        return SimulatorSource(sim_url, clock=clock)
    if backend == "kraken":
        return KrakenSource(ws_url, clock=clock)
    raise ValueError(f"unknown kraken backend {backend!r}")
