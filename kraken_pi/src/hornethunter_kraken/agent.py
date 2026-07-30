"""Station agent — the poll-answering loop (FSD §2.1, §9, §10, §13).

The station **never initiates** (§2.1): it reads bytes from the carrier into a
`FrameReader` and reacts to what the master sends. On a `POLL` addressed to its slot
it replies with a `BEARING` (the poll response, not ARQ-acked). On a `PARAM_DELTA`
it applies the change through the settings client and ACKs with the observed config
version and CRC, using a `DedupReceiver` so a retransmitted delta is applied once
(FR-10.7). On a `PARAM_REQ` it replies with a fragmented `PARAM_REPORT`.

The agent keeps answering polls even when the DoA feed or the settings endpoint is
down (§2.3, FR-13.4): a dead feed yields a `no_data` record and a dead settings
endpoint is reported, never crashed. `step(now_ms)` pumps one round and is
host-testable over `InProcessLink` with a `SyntheticSource` and a `FakeTransport`.
"""

from __future__ import annotations

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
from hornethunter_shared.protocol import PollPayload, fragment
from hornethunter_shared.registry import (
    BY_KEY,
    decode_delta,
    encode_delta,
)

from .doa_source import DoaSource
from .measurement import Measurement
from .pipeline import BearingPipeline
from .settings_client import KrakenSettings

_FRAG_CHUNK = MAX_PAYLOAD - 2  # leave room for the 2-byte fragment header


def _cfg(config: dict[str, Any], section: str, key: str, default: Any) -> Any:
    return config.get(section, {}).get(key, default)


class StationAgent:
    """Wires a carrier, a DoA source and the settings client into the poll loop."""

    def __init__(
        self,
        carrier: Carrier,
        config: dict[str, Any],
        source: DoaSource,
        settings: KrakenSettings,
        *,
        slot_index: int,
        address: int,
        reference: LatLon | None = None,
        pipeline: BearingPipeline | None = None,
        clock: Callable[[], float] = time.monotonic,
        logger: Any | None = None,
    ) -> None:
        self._carrier = carrier
        self._source = source
        self._settings = settings
        self._slot_index = slot_index
        self._address = address
        self._clock = clock
        self._logger = logger

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

    # -- loop ----------------------------------------------------------------

    def step(self, now: float | None = None) -> None:
        """Pump one round: advance the source, read the carrier, answer frames."""
        now = self._clock() if now is None else now
        self._pump_source()
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

    # -- dispatch ------------------------------------------------------------

    def _dispatch(self, frame: Frame, now: float) -> None:
        if frame.msg_type == MsgType.POLL:
            self._on_poll(frame, now)
        elif frame.msg_type == MsgType.PARAM_DELTA:
            self._on_param_delta(frame)
        elif frame.msg_type == MsgType.PARAM_REQ:
            self._on_param_req(frame)
        # Other types (ACK, IDENT, PARAM_FULL/REPORT) are not initiated at a station.

    def _on_poll(self, frame: Frame, now: float) -> None:
        try:
            poll = PollPayload.decode(frame.payload)
        except Exception:  # noqa: BLE001 - a malformed poll is ignored, not fatal
            return
        if not poll.expects(self._slot_index):
            return
        report = self._pipeline.build(
            config_version=self._settings.config_version,
            config_crc=self._settings.config_crc,
            link_up=self._source.available,
            now=now,
        )
        payload = encode_bearing(report, self._reference)
        self._send(Frame(MsgType.BEARING, dest=frame.src, src=self._address,
                         seq=frame.seq, payload=payload))
        self._pipeline.reset_counts()

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
