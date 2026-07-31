"""KrakenProxy — the bearing-streaming loop (FSD §5, §6, §10, §11, §14).

The station **streams** (§5): on each new direction estimate from its DoA source it
transmits a `BEARING` autonomously — no poll, no acknowledgement — rate-limited to
`max_rate_hz` and with a heartbeat so a live station with no signal still shows as
present. It also **receives** the master's configuration traffic: on a `PARAM_DELTA`
it applies the change through the settings client and ACKs with the observed config
version and CRC (a `DedupReceiver` so a retransmitted delta is applied once,
FR-10.7); on a `PARAM_REQ` it replies with a fragmented `PARAM_REPORT`.

The agent keeps streaming even when the DoA feed or the settings endpoint is down
(§2.3, FR-13.4): a dead feed yields a `no_data` record and a dead settings endpoint
is reported, never crashed. `step(now)` pumps one round and is host-testable over
`InProcessLink` with a `SyntheticSource` and a `FakeTransport`.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any

from hornethunter_shared.arq import DedupReceiver, make_ack
from hornethunter_shared.bearing import encode_bearing
from hornethunter_shared.carrier import Carrier
from hornethunter_shared.frame import (
    MAX_PAYLOAD,
    Frame,
    FrameReader,
    MsgType,
)
from hornethunter_shared.geo import LatLon
from hornethunter_shared.protocol import BeaconPayload, JoinPayload, fragment
from hornethunter_shared.registry import (
    BY_KEY,
    decode_delta,
    encode_delta,
)
from hornethunter_shared.schedule import Superframe, join_offset_ms

from .doa_source import DoaSource
from .measurement import Measurement
from .pipeline import BearingPipeline
from .settings_client import KrakenSettings
from .supervisor import Supervisor

_FRAG_CHUNK = MAX_PAYLOAD - 2  # leave room for the 2-byte fragment header
MASTER_ADDR = 0xFF  # HH-Link destination for a streamed bearing (§18.2)


def _cfg(config: dict[str, Any], section: str, key: str, default: Any) -> Any:
    return config.get(section, {}).get(key, default)


class KrakenProxy:
    """Wires a carrier, a DoA source and the settings client into the streaming loop."""

    def __init__(
        self,
        carrier: Carrier,
        config: dict[str, Any],
        source: DoaSource,
        settings: KrakenSettings,
        *,
        address: int,
        max_rate_hz: float = 5.0,
        heartbeat_s: float = 0.5,
        reference: LatLon | None = None,
        pipeline: BearingPipeline | None = None,
        clock: Callable[[], float] = time.monotonic,
        logger: Any | None = None,
        join_rng: Callable[[], float] = random.random,
        supervisor: Supervisor | None = None,
    ) -> None:
        self._carrier = carrier
        self._supervisor = supervisor
        self._source = source
        self._settings = settings
        self._address = address
        self._clock = clock
        self._logger = logger
        self._min_interval = 1.0 / max_rate_hz if max_rate_hz > 0 else 0.0
        self._heartbeat_s = heartbeat_s
        self._last_tx = float("-inf")
        self._last_sent: Measurement | None = None

        if reference is None:
            lat = _cfg(config, "bearing", "reference_lat", None)
            lon = _cfg(config, "bearing", "reference_lon", None)
            if lat is not None and lon is not None:
                reference = LatLon(float(lat), float(lon))
        self._reference = reference

        self._pipeline = pipeline or BearingPipeline(
            station_id=str(_cfg(config, "station", "id", "unknown")),
            reference=reference,
            position_epsilon_dm=int(_cfg(config, "bearing", "position_epsilon_dm", 50)),
            max_age_ms=int(_cfg(config, "bearing", "max_age_ms", 5000)),
            clock=clock,
        )
        self._reader = FrameReader(rssi_appended=bool(_cfg(config, "link", "rssi_append", False)))
        self._dedup = DedupReceiver()
        self._last_measurement: Measurement | None = None
        self._tx_seq = 0

        # TDMA participation (§5, §6). Off by default so behaviour is unchanged; the
        # geometry must match the master's [tdma] config. Timing-zero is the beacon.
        self._tdma_enabled = bool(_cfg(config, "tdma", "enabled", False))
        self._superframe = Superframe(
            period_ms=int(_cfg(config, "tdma", "period_ms", 1000)),
            slot_ms=int(_cfg(config, "tdma", "slot_ms", 125)),
            guard_ms=int(_cfg(config, "tdma", "guard_ms", 25)),
            max_slots_per_station=int(_cfg(config, "tdma", "max_slots_per_station", 2)),
        )
        self._beacon_loss_ms = 2 * self._superframe.period_ms
        self._join_rng = join_rng
        self._beacon_t0: float | None = None
        self._beacon_seq: int | None = None
        self._my_slots: tuple[int, ...] = ()
        self._joined = False
        self._sent_slots: set[int] = set()
        self._join_sent_seq: int | None = None
        self._join_offset_ms = 0

    # -- loop ----------------------------------------------------------------

    def step(self, now: float | None = None) -> None:
        """Pump one round: advance the source, transmit per schedule, service config."""
        now = self._clock() if now is None else now
        self._pump_source()
        if self._supervisor is not None:
            if self._source.available:
                self._supervisor.note_alive(now)  # DSP reachable (even if squelched)
            self._supervisor.tick(now)  # bounded autorecovery on a stalled feed (§5.5)
        if self._tdma_enabled:
            self._drain_incoming(now)  # apply a fresh beacon before deciding to transmit
            self._tdma_tick(now)
        else:
            self._maybe_stream(now)
            self._drain_incoming(now)

    def _drain_incoming(self, now: float) -> None:
        try:
            data = self._carrier.recv()
        except OSError:
            return
        for frame in self._reader.feed(data):
            self._dispatch(frame, now)

    def run_forever(self, *, idle_s: float = 0.02) -> None:  # pragma: no cover - real loop
        while True:
            self.step()
            time.sleep(idle_s)

    # -- source --------------------------------------------------------------

    def _pump_source(self) -> None:
        try:
            self._source.pump()
        except Exception as exc:  # noqa: BLE001 - a dead feed must not stall the loop (§2.3)
            self._log("doa_pump_error", error=str(exc))
            return
        measurement = self._source.latest()
        if measurement is not None and measurement is not self._last_measurement:
            self._last_measurement = measurement
            self._pipeline.update(measurement)

    # -- streaming (§5) ------------------------------------------------------

    def _maybe_stream(self, now: float) -> None:
        """Free-running transmit (non-TDMA, §5): a BEARING on a new estimate,
        rate-limited, plus a heartbeat so a live station with no fresh measurement
        still shows present (§9.6)."""
        elapsed = now - self._last_tx
        if elapsed < self._min_interval:
            return
        new_measurement = (
            self._last_measurement is not None and self._last_measurement is not self._last_sent
        )
        if not (new_measurement or elapsed >= self._heartbeat_s):
            return
        self._transmit_bearing(now)

    def _transmit_bearing(self, now: float) -> None:
        """Build and send one BEARING frame (§9.3), stamping the tx bookkeeping."""
        report = self._pipeline.build(
            config_version=self._settings.config_version,
            config_crc=self._settings.config_crc,
            link_up=self._source.available,
            now=now,
        )
        payload = encode_bearing(report, self._reference)
        self._send(
            Frame(MsgType.BEARING, dest=MASTER_ADDR, src=self._address,
                  seq=self._tx_seq & 0xFF, payload=payload)
        )
        self._tx_seq = (self._tx_seq + 1) & 0xFF
        self._last_tx = now
        self._last_sent = self._last_measurement
        self._pipeline.reset_counts()

    # -- TDMA participation (§5, §6) -----------------------------------------

    def _on_beacon(self, frame: Frame, now: float) -> None:
        """Sync to the master's beacon: its arrival is superframe timing-zero (§5.2)."""
        try:
            beacon = BeaconPayload.decode(frame.payload)
        except Exception:  # noqa: BLE001 - a malformed beacon must not stall the loop
            return
        self._beacon_t0 = now
        if beacon.seq != self._beacon_seq:
            self._beacon_seq = beacon.seq
            self._sent_slots = set()
            self._join_sent_seq = None
            self._join_offset_ms = join_offset_ms(self._superframe, self._join_rng())
        self._my_slots = beacon.slots_for(self._address)
        self._joined = bool(self._my_slots)

    def _tdma_tick(self, now: float) -> None:
        """Transmit only in this station's slot(s); JOIN if unslotted; go silent on
        beacon loss (§5.2, §5.3, FR-5.2/5.5/5.7)."""
        if self._beacon_t0 is None:
            return  # no timing reference yet — never transmit blind (FR-5.2)
        elapsed_ms = (now - self._beacon_t0) * 1000.0
        if elapsed_ms > self._beacon_loss_ms:
            if self._beacon_seq is not None:
                self._log("beacon_lost")  # go silent, keep measuring, rejoin (FR-5.7)
            self._beacon_t0 = None
            self._beacon_seq = None
            self._joined = False
            return
        if not self._joined:
            self._maybe_join(now, elapsed_ms)
            return
        for slot in self._my_slots:
            start, end = self._superframe.data_slot_window(slot)
            if start <= elapsed_ms < end and slot not in self._sent_slots:
                self._transmit_bearing(now)
                self._sent_slots.add(slot)

    def _maybe_join(self, now: float, elapsed_ms: float) -> None:
        """Send one JOIN in the contention window per superframe, at a backoff offset."""
        if self._join_sent_seq == self._beacon_seq:
            return
        if elapsed_ms >= self._join_offset_ms:
            ref = self._reference
            join = JoinPayload(
                self._tx_seq & 0xFF,
                ref.lat if ref is not None else None,
                ref.lon if ref is not None else None,
            )
            self._send(
                Frame(MsgType.JOIN, dest=MASTER_ADDR, src=self._address,
                      seq=self._tx_seq & 0xFF, payload=join.encode())
            )
            self._tx_seq = (self._tx_seq + 1) & 0xFF
            self._join_sent_seq = self._beacon_seq
            self._log("join_sent", seq=self._beacon_seq)

    # -- config dispatch (received from the master) --------------------------

    def _dispatch(self, frame: Frame, now: float) -> None:
        if frame.msg_type == MsgType.PARAM_DELTA:
            self._on_param_delta(frame)
        elif frame.msg_type == MsgType.PARAM_REQ:
            self._on_param_req(frame)
        elif frame.msg_type == MsgType.BEACON:
            self._on_beacon(frame, now)
        # BEARING/ACK/IDENT are never initiated by the master toward a station.

    def _on_param_delta(self, frame: Frame) -> None:
        is_new = self._dedup.accept(frame)
        if is_new:
            try:
                changes = decode_delta(frame.payload)
            except (KeyError, ValueError, IndexError):
                self._log("param_delta_malformed", seq=frame.seq)
                return
            self._settings.apply_delta(changes)
        # Duplicate: re-ACK with the current observed state, apply only once (FR-10.7).
        ack = make_ack(
            dest=frame.src,
            src=self._address,
            acked_seq=frame.seq,
            config_version=self._settings.config_version,
            config_crc=self._settings.config_crc,
        )
        self._send(ack)

    def _on_param_req(self, frame: Frame) -> None:
        try:
            settings = self._settings.read()
        except OSError:
            settings = {}
        full = encode_delta({key: settings[key] for key in settings if key in BY_KEY})
        for index, chunk in enumerate(fragment(full, _FRAG_CHUNK)):
            self._send(
                Frame(
                    MsgType.PARAM_REPORT,
                    dest=frame.src,
                    src=self._address,
                    seq=(frame.seq + index) & 0xFF,
                    payload=chunk,
                )
            )

    # -- carrier -------------------------------------------------------------

    def _send(self, frame: Frame) -> None:
        try:
            self._carrier.send(frame.encode())
        except OSError as exc:
            self._log("carrier_send_error", error=str(exc))

    def _log(self, event: str, **fields: Any) -> None:
        if self._logger is not None:
            self._logger.event(event, **fields)
