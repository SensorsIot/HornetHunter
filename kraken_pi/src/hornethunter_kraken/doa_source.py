"""Kraken DoA source interface (FSD §12).

Three backends present **one** internal `Measurement` type; no caller branches on
backend (FR-12.3). The simulator and the real KrakenSDR use different field names
and transports, so a pure, table-driven adapter maps each raw record to a
`Measurement` (NFR-12.1); the mapping is tested with plain dicts, no network.

* `SyntheticSource` — deterministic in-process generator, no I/O (host tier).
* `SimulatorSource` — HTTP `GET /api/v1/doa` (the `KrakenSimulator`).
* `KrakenSource` — HTTP `GET /DOA_value.html` on the KrakenSDR node server (real
  hardware). This is the KrakenSDR's only documented local DoA interface: the DSP
  rewrites the CSV file every update for every DoA data format except "Kerberos
  App" (see `_sdr/_signal_processing/kraken_sdr_signal_processor.py`), and the node
  server on port 8081 serves it. There is no WebSocket DoA feed on any KrakenSDR.

Network sources reconnect with capped exponential backoff (FR-12.1), expose feed
availability as explicit state (FR-12.2), and discard-and-count malformed or
missing-field records (FR-12.4).
"""

from __future__ import annotations

import abc
import json
import math
import random
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from hornethunter_shared.geo import LatLon, distance_m, initial_bearing_deg, normalize_bearing

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


# §12.3 adapter table. The simulator emits JSON; the real KrakenSDR emits positional
# CSV (see `parse_doa_csv`), so only the simulator uses a name-keyed adapter.
SIMULATOR_ADAPTER = MeasurementAdapter(
    bearing="bearing_deg", quality="width_rad", power="rssi_dbfs", frequency="center_freq_hz"
)

# The KrakenSDR DoA node server serves the live CSV here (all formats but Kerberos App).
DEFAULT_DOA_URL = "http://127.0.0.1:8081/DOA_value.html"


def parse_doa_csv(text: str, *, mono_ts: float) -> tuple[int, Measurement] | None:
    """Parse the KrakenSDR `DOA_value.html` CSV into `(timestamp_ms, Measurement)`.

    Field order is fixed by the DSP writer (`kraken_sdr_signal_processor.py`, the
    "Kraken App" branch)::

        ts_ms, bearing_deg, confidence, max_power_dBm, freq_Hz, array, latency_ms,
        station_id, lat, lon, heading, heading, "GPS", R, R, R, R, <360 spectrum...>

    `bearing_deg` is already compass convention (the DSP writes ``360 - theta``).
    The file holds one line per active VFO, rewritten in place each update; we take
    the last non-empty line. An empty file (squelch closed, no DoA output) returns
    ``None`` — the feed is up but has produced nothing, not an error.
    """
    line = ""
    for candidate in text.splitlines():
        if candidate.strip():
            line = candidate
    if not line.strip():
        return None
    fields = [f.strip() for f in line.split(",")]
    if len(fields) < 5:
        raise AdapterError(f"DOA CSV line has {len(fields)} fields, need >=5")
    try:
        timestamp_ms = int(float(fields[0]))
        measurement = Measurement(
            bearing_deg=float(fields[1]),
            confidence=float(fields[2]),
            power_dbm=float(fields[3]),
            freq_hz=float(fields[4]),
            latitude=_csv_float(fields, 8),
            longitude=_csv_float(fields, 9),
            adc_overdrive=False,
            squelch_open=True,  # a written line means the VFO passed squelch
            num_corr_sources=0,
            snr=0.0,
            mono_ts=mono_ts,
        )
    except (TypeError, ValueError) as exc:
        raise AdapterError(f"unparseable DOA CSV line: {line!r}") from exc
    return timestamp_ms, measurement


def _csv_float(fields: list[str], index: int) -> float | None:
    if index >= len(fields):
        return None
    try:
        return float(fields[index])
    except (TypeError, ValueError):
        return None


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


class VirtualTargetSource(DoaSource):
    """In-process Kraken **network simulator**: observe a virtual transmitter (§12.3).

    Runs inside the ordinary `KrakenProxy` in place of a real KrakenSDR, so a station
    with no hardware behaves exactly like a real one on the LoRa network — same
    frames, same TDMA slots, same health. It computes the true great-circle bearing
    from the station's own position to a configured target, adds mild Gaussian
    jitter, and emits a realistic `Measurement` at the KrakenSDR's ~2.3 Hz cadence.
    Point several simulated stations at the **same** target and their bearings
    triangulate onto it — a full 3-station + Management Pi network with one real
    Kraken and the rest simulated. Deterministic for a given `seed`.
    """

    def __init__(
        self,
        station: LatLon,
        target: LatLon,
        *,
        clock: Callable[[], float] = time.monotonic,
        update_rate_hz: float = 2.3,
        bearing_noise_deg: float = 1.5,
        freq_hz: float = 148_524_000.0,
        seed: int = 0,
    ) -> None:
        super().__init__()
        self._station = station
        self._target = target
        self._clock = clock
        self._interval = 1.0 / update_rate_hz if update_rate_hz > 0 else 0.0
        self._noise = bearing_noise_deg
        self._freq = freq_hz
        self._rng = random.Random(seed)
        self._next_update = 0.0
        self.available = True

    def pump(self) -> None:
        now = self._clock()
        if now < self._next_update:
            return
        self._next_update = now + self._interval
        true_bearing = initial_bearing_deg(self._station, self._target)
        bearing = normalize_bearing(true_bearing + self._rng.gauss(0.0, self._noise))
        dist = distance_m(self._station, self._target)
        # Plausible confidence/power/SNR that fade with range (display only, §9.3).
        confidence = max(5.0, 100.0 - dist / 20.0) + self._rng.uniform(-3.0, 3.0)
        power_dbm = -40.0 - 20.0 * math.log10(max(dist, 1.0) / 100.0)
        self._latest = Measurement(
            bearing_deg=bearing,
            confidence=confidence,
            power_dbm=power_dbm,
            freq_hz=self._freq,
            latitude=self._station.lat,
            longitude=self._station.lon,
            adc_overdrive=False,
            squelch_open=True,
            num_corr_sources=4,
            snr=max(0.0, 20.0 - dist / 500.0),
            mono_ts=now,
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
    """Real KrakenSDR backend: poll the node server's `DOA_value.html` CSV (§12.2, §12.3).

    The KrakenSDR has no local push feed; the DSP rewrites a CSV file every update
    and the node server serves it over HTTP. We GET it, parse the last line, and
    emit a `Measurement` only when the DSP's own timestamp advances — an unchanged
    or empty file is a valid "no new bearing" state (squelch closed / no signal),
    not a fault. Reconnect uses capped backoff (FR-12.1); malformed lines are
    discarded and counted (FR-12.4).
    """

    def __init__(
        self,
        url: str = DEFAULT_DOA_URL,
        *,
        clock: Callable[[], float] = time.monotonic,
        opener: Callable[[str], Any] | None = None,
        timeout_s: float = 1.0,
        poll_interval_s: float = 0.1,
    ) -> None:
        super().__init__()
        self._url = url
        self._clock = clock
        self._opener = opener or (lambda u: urllib.request.urlopen(u, timeout=timeout_s))
        self._backoff = Backoff()
        self._next_attempt = 0.0
        self._poll_interval_s = poll_interval_s
        self._last_timestamp_ms: int | None = None

    def pump(self) -> None:
        now = self._clock()
        if now < self._next_attempt:
            return
        self._next_attempt = now + self._poll_interval_s
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
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        try:
            parsed = parse_doa_csv(text, mono_ts=self._clock())
        except AdapterError:
            self.discarded += 1
            return
        if parsed is None:
            return  # empty file: feed up, no bearing produced (squelch closed)
        timestamp_ms, measurement = parsed
        if timestamp_ms == self._last_timestamp_ms:
            return  # same DSP frame already emitted; not a new measurement
        self._last_timestamp_ms = timestamp_ms
        self._latest = measurement
        self.produced += 1


def build_source(
    backend: str,
    *,
    doa_url: str = DEFAULT_DOA_URL,
    sim_url: str = "http://127.0.0.1:8080/api/v1/doa",
    clock: Callable[[], float] = time.monotonic,
    latitude: float | None = None,
    longitude: float | None = None,
    target_lat: float | None = None,
    target_lon: float | None = None,
    bearing_noise_deg: float = 1.5,
    update_rate_hz: float = 2.3,
    seed: int = 0,
) -> DoaSource:
    """Construct the configured backend (§12.3). All present one internal type."""
    if backend == "synthetic":
        return SyntheticSource(clock=clock, latitude=latitude, longitude=longitude)
    if backend == "virtual":
        if latitude is None or longitude is None or target_lat is None or target_lon is None:
            raise ValueError(
                "virtual backend needs station [station] lat/lon and [simulator] target_lat/lon"
            )
        return VirtualTargetSource(
            LatLon(latitude, longitude),
            LatLon(target_lat, target_lon),
            clock=clock,
            update_rate_hz=update_rate_hz,
            bearing_noise_deg=bearing_noise_deg,
            seed=seed,
        )
    if backend == "simulator":
        return SimulatorSource(sim_url, clock=clock)
    if backend == "kraken":
        return KrakenSource(doa_url, clock=clock)
    raise ValueError(f"unknown kraken backend {backend!r}")
