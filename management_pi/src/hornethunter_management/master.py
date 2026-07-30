"""Master loop wiring (FSD §2.1, and the L2 chapters it drives).

`Master` **receives** streamed BEARING frames (§5, §9.3) over a `Carrier` (§15.2),
feeds each arrival to the `HealthEvaluator` (§8, staleness) and the UI state store,
and runs the `ParameterDistributor` (§7) over a `StopAndWaitSender` (§10.5) per
station for the acknowledged **configuration** path — the only traffic the master
initiates. Bearings are never polled for and never acknowledged. `step(now_ms)`
advances the machine off an injected clock so it is host-testable over
`InProcessLink` (§24.1).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from hornethunter_shared.arq import StopAndWaitSender, TxEvent
from hornethunter_shared.bearing import decode_bearing
from hornethunter_shared.carrier import Carrier
from hornethunter_shared.frame import FrameReader, MsgType
from hornethunter_shared.geo import LatLon
from hornethunter_shared.messages import BearingReport
from hornethunter_shared.protocol import AckPayload, Reassembler
from hornethunter_shared.registry import decode_delta

from .health import HealthEvaluator, HealthSnapshot
from .mirror import ConfigMirror
from .param_dist import ConfigSnapshot, ParameterDistributor, PendingPush
from .transport import CarrierKind, TransportSelector

# The master's HH-Link address (§18.2): 0xFFFF-class broadcast — it hears every
# station's stream and its config frames reach every station.
BROADCAST_ADDR = 0xFF


@dataclass(frozen=True)
class StationSpec:
    """Identity and geometry for one station (§18.1)."""

    addr: int
    name: str
    reference: LatLon | None = None


@dataclass(frozen=True)
class MasterConfig:
    """Everything the master needs, decoupled from the TOML layout so tests can
    build one directly."""

    stations: tuple[StationSpec, ...]
    # Health (§8): staleness-based, fed by bearing arrivals.
    staleness_threshold_s: float = 1.0
    rate_window_s: float = 10.0
    expected_rate_hz: float = 2.3
    orange_rate_fraction: float = 0.5
    # Configuration ARQ (§10.5): the only acknowledged path.
    arq_timeout_ms: int = 1000
    arq_max_attempts: int = 5
    # Transport selection (§6).
    promote_probes: int = 3
    demote_probes: int = 2
    dwell_s: float = 30.0
    master_addr: int = BROADCAST_ADDR
    rssi_appended: bool = False

    @classmethod
    def from_toml(cls, config: dict[str, Any]) -> MasterConfig:
        """Build from a parsed management TOML (§19.2)."""
        stations: list[StationSpec] = []
        for entry in config.get("station", []):
            ref = None
            if "reference_lat" in entry and "reference_lon" in entry:
                ref = LatLon(float(entry["reference_lat"]), float(entry["reference_lon"]))
            stations.append(
                StationSpec(
                    addr=int(entry["address"]),
                    name=str(entry.get("name", f"station-{entry['address']}")),
                    reference=ref,
                )
            )
        health = config.get("health", {})
        arq = config.get("arq", {})
        carrier = config.get("carrier", {})
        return cls(
            stations=tuple(stations),
            staleness_threshold_s=float(health.get("staleness_threshold_s", 1.0)),
            rate_window_s=float(health.get("rate_window_s", 10.0)),
            expected_rate_hz=float(health.get("expected_rate_hz", 2.3)),
            orange_rate_fraction=float(health.get("orange_rate_fraction", 0.5)),
            arq_timeout_ms=int(arq.get("timeout_ms", 1000)),
            arq_max_attempts=int(arq.get("max_attempts", 5)),
            promote_probes=int(carrier.get("promote_probes", 3)),
            demote_probes=int(carrier.get("demote_probes", 2)),
            dwell_s=float(carrier.get("dwell_s", 30.0)),
            rssi_appended=bool(config.get("link", {}).get("rssi_append", False)),
        )


@dataclass
class StationState:
    """The mutable per-station view the UI reads (FR-14.1, FR-21.5)."""

    spec: StationSpec
    carrier: CarrierKind = CarrierKind.LORA
    pinned: CarrierKind | None = None
    last_bearing: BearingReport | None = None
    last_bearing_wall: float | None = None
    discards: int = 0
    crc_failures: int = 0
    unconfigured_frames: int = 0


@dataclass
class _StationRuntime:
    sender: StopAndWaitSender
    health: HealthEvaluator
    reassembler: Reassembler = field(default_factory=Reassembler)
    outbox: deque[tuple[MsgType, bytes, PendingPush | None]] = field(default_factory=deque)
    inflight: PendingPush | None = None
    desired: dict[str, Any] | None = None


class Master:
    """Composition root: bearing ingest, staleness health, transport and the
    acknowledged configuration path."""

    def __init__(
        self,
        carrier: Carrier,
        config: MasterConfig,
        mirror: ConfigMirror,
        *,
        logger: Any | None = None,
    ) -> None:
        self.carrier = carrier
        self.config = config
        self.mirror = mirror
        self.logger = logger
        self._reader = FrameReader(rssi_appended=config.rssi_appended)
        self.transport = TransportSelector(
            promote_probes=config.promote_probes,
            demote_probes=config.demote_probes,
            dwell_s=config.dwell_s,
        )
        self.params = ParameterDistributor(mirror)
        self.states: dict[int, StationState] = {}
        self._runtime: dict[int, _StationRuntime] = {}
        self._now_s = 0.0
        for spec in config.stations:
            self.states[spec.addr] = StationState(spec=spec)
            self._runtime[spec.addr] = _StationRuntime(
                sender=StopAndWaitSender(
                    dest=spec.addr,
                    src=config.master_addr,
                    timeout_ms=config.arq_timeout_ms,
                    max_attempts=config.arq_max_attempts,
                ),
                health=HealthEvaluator(
                    staleness_threshold_s=config.staleness_threshold_s,
                    rate_window_s=config.rate_window_s,
                    expected_rate_hz=config.expected_rate_hz,
                    orange_rate_fraction=config.orange_rate_fraction,
                ),
            )

    def _log(self, event: str, **fields: Any) -> None:
        if self.logger is not None:
            self.logger.event(event, **fields)

    # -- operator entry points (configuration path) ------------------------

    def queue_delta(self, addr: int, desired: dict[str, Any]) -> None:
        """Queue a field edit as a delta into the pending change slot (FR-14.5)."""
        self._runtime[addr].desired = desired

    def request_full_read(self, addr: int) -> None:
        """Send a PARAM_REQ full-set read (FR-14.6, FR-7.8)."""
        self._runtime[addr].outbox.append((MsgType.PARAM_REQ, b"", None))

    def push_full(self, addr: int) -> None:
        """Explicit manual full-set push (FR-14.6, FR-7.8)."""
        push = self.params.prepare_full_push(addr)
        if push is not None:
            self._enqueue_push(addr, push)

    def revert(self, addr: int) -> dict[str, Any] | None:
        """Revert to the last known-good snapshot and push it (FR-14.7, §7.6)."""
        restored = self.mirror.revert(addr)
        if restored is not None:
            self.push_full(addr)
        self._log("config_revert", station=addr, reverted=restored is not None)
        return restored

    def pin_carrier(self, addr: int, carrier: CarrierKind | None) -> None:
        """Pin (or release) the carrier for a station (FR-14.8, FR-6.5)."""
        self.transport.set_pin(addr, carrier)
        self.states[addr].pinned = carrier
        self.states[addr].carrier = self.transport.active(addr)

    def on_probe(self, addr: int, success: bool, now_ms: int) -> None:
        """Feed a WLAN-reachability probe; a carrier change is logged."""
        result = self.transport.on_probe(addr, success, now_ms)
        self.states[addr].carrier = result.active
        if result.changed:
            self._log("carrier_switch", station=addr, carrier=result.active.value)

    # -- state store for the UI --------------------------------------------

    def health_snapshot(self, addr: int) -> HealthSnapshot:
        return self._runtime[addr].health.snapshot(self._now_s)

    def config_snapshot(self, addr: int) -> ConfigSnapshot:
        return self.params.snapshot(addr)

    # -- the loop ----------------------------------------------------------

    def step(self, now_ms: int) -> None:
        """Advance one tick: drain incoming frames, service the configuration ARQ."""
        self._now_s = now_ms / 1000.0
        self._receive(now_ms)
        self._service_arq(now_ms)

    def _receive(self, now_ms: int) -> None:
        data = self.carrier.recv()
        if not data:
            return
        for frame in self._reader.feed(data):
            self._dispatch(frame, now_ms)

    def _dispatch(self, frame: Any, now_ms: int) -> None:
        if frame.msg_type is MsgType.BEARING:
            self._on_bearing(frame, now_ms)
        elif frame.msg_type is MsgType.ACK:
            self._on_ack(frame, now_ms)
        elif frame.msg_type is MsgType.PARAM_REPORT:
            self._on_param_report(frame)
        # Stray POLL (legacy) and IDENT are ignored by the master.

    def _on_bearing(self, frame: Any, now_ms: int) -> None:
        addr = frame.src
        if addr not in self.states:
            self._log("unconfigured_frame", src=addr)
            return
        state = self.states[addr]
        try:
            report = decode_bearing(frame.payload, state.spec.name, state.spec.reference)
        except ValueError:
            state.crc_failures += 1
            return
        state.last_bearing = report
        state.last_bearing_wall = time.time()
        state.carrier = self.transport.active(addr)
        self._runtime[addr].health.record_bearing(now_ms / 1000.0, rssi_dbm=report.power_dbm)
        self.params.observe_bearing(addr, report.config_version, report.config_crc)

    def _on_ack(self, frame: Any, now_ms: int) -> None:
        addr = frame.src
        runtime = self._runtime.get(addr)
        if runtime is None:
            return
        if not runtime.sender.on_ack(frame.seq):
            return
        ack = AckPayload.decode(frame.payload) if frame.payload else AckPayload(frame.seq)
        push = runtime.inflight
        runtime.inflight = None
        if push is not None and push.kind in ("delta", "full"):
            result = self.params.on_ack(
                addr, ack.config_version, ack.config_crc, status=ack.status
            )
            self._log(
                "param_ack",
                station=addr,
                state=result.state.value,
                committed=result.committed,
                config_version=ack.config_version,
            )
            if result.auto_push is not None:
                self._enqueue_push(addr, result.auto_push)

    def _on_param_report(self, frame: Any) -> None:
        addr = frame.src
        runtime = self._runtime.get(addr)
        if runtime is None:
            return
        assembled = runtime.reassembler.add(frame.payload)
        if assembled is None:
            return
        settings = decode_delta(assembled)
        self.params.on_full_report(addr, settings)
        self._log("param_report", station=addr, fields=len(settings))

    # -- configuration ARQ servicing (§10.5) -------------------------------

    def _enqueue_push(self, addr: int, push: PendingPush) -> None:
        msg_type = MsgType.PARAM_DELTA if push.kind == "delta" else MsgType.PARAM_FULL
        self._runtime[addr].outbox.append((msg_type, push.payload, push))

    def _service_arq(self, now_ms: int) -> None:
        for addr, runtime in self._runtime.items():
            sender = runtime.sender
            if sender.busy and now_ms >= sender.deadline_ms:
                event, frame = sender.on_timeout(now_ms)
                if event is TxEvent.RETRANSMIT and frame is not None:
                    self.carrier.send(frame.encode())
                    self._log("arq_retransmit", station=addr, seq=frame.seq)
                elif event is TxEvent.EXHAUSTED:
                    runtime.inflight = None
                    self._log("arq_exhausted", station=addr)
            # Author a queued delta lazily so it reflects the freshest mirror.
            if not sender.busy and runtime.desired is not None and not runtime.outbox:
                self._author_delta(addr, runtime)
            if not sender.busy and runtime.outbox:
                msg_type, payload, push = runtime.outbox.popleft()
                out = sender.start(msg_type, payload, now_ms)
                runtime.inflight = push
                self.carrier.send(out.encode())
                self._log("tx", station=addr, type=msg_type.name, seq=out.seq)

    def _author_delta(self, addr: int, runtime: _StationRuntime) -> None:
        desired = runtime.desired
        runtime.desired = None
        if desired is None:
            return
        try:
            push = self.params.prepare_delta(addr, desired)
        except Exception as exc:  # NoBaselineError and malformed values
            self._log("param_delta_rejected", station=addr, reason=str(exc))
            return
        if push is not None:
            self._enqueue_push(addr, push)
