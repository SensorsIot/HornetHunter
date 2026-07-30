"""Master loop wiring (FSD §2.1, and the L2 chapters it drives).

`Master` drives the `PollScheduler` (§5) over a `Carrier` (§15.2) with a
`StopAndWaitSender` (§10.5) per station, decodes incoming BEARING frames (§9.3),
feeds per-cycle outcomes to the `HealthEvaluator` (§8) and runs the
`ParameterDistributor` (§7) for pending changes. It maintains a state store —
latest bearing, health, carrier and configuration state per station — for the UI
to read (§14). `step(now_ms)` advances the whole machine off an injected clock so
it is host-testable over `InProcessLink` (§24.1).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from hornethunter_shared.arq import StopAndWaitSender, TxEvent
from hornethunter_shared.bearing import decode_bearing
from hornethunter_shared.carrier import Carrier
from hornethunter_shared.frame import Frame, FrameReader, MsgType
from hornethunter_shared.geo import LatLon
from hornethunter_shared.messages import BearingReport
from hornethunter_shared.protocol import AckPayload, Reassembler
from hornethunter_shared.registry import decode_delta

from .health import CycleOutcome, HealthEvaluator, HealthSnapshot
from .mirror import ConfigMirror
from .param_dist import ConfigSnapshot, ParameterDistributor, PendingPush
from .scheduler import BROADCAST_ADDR, CycleTiming, PollScheduler
from .transport import CarrierKind, TransportSelector


@dataclass(frozen=True)
class StationSpec:
    """Identity and geometry for one station (§18.1)."""

    addr: int
    name: str
    slot_index: int
    reference: LatLon | None = None


@dataclass(frozen=True)
class MasterConfig:
    """Everything the master needs, decoupled from the TOML layout so tests can
    build one directly."""

    stations: tuple[StationSpec, ...]
    timing: CycleTiming = CycleTiming(period_ms=1000, guard_ms=40, slot_ms=150)
    arq_timeout_ms: int = 400
    arq_max_attempts: int = 3
    window_cycles: int = 20
    retry_rate_threshold: float = 0.20
    stale_cycles: int = 5
    promote_probes: int = 3
    demote_probes: int = 2
    dwell_s: float = 30.0
    master_addr: int = BROADCAST_ADDR
    rssi_appended: bool = False

    @classmethod
    def from_toml(cls, config: dict[str, Any]) -> MasterConfig:
        """Build from a parsed management TOML (§19.2)."""
        stations: list[StationSpec] = []
        for index, entry in enumerate(config.get("station", [])):
            ref = None
            if "reference_lat" in entry and "reference_lon" in entry:
                ref = LatLon(float(entry["reference_lat"]), float(entry["reference_lon"]))
            stations.append(
                StationSpec(
                    addr=int(entry["address"]),
                    name=str(entry.get("name", f"station-{entry['address']}")),
                    slot_index=int(entry.get("slot_index", index)),
                    reference=ref,
                )
            )
        cycle = config.get("cycle", {})
        arq = config.get("arq", {})
        health = config.get("health", {})
        carrier = config.get("carrier", {})
        return cls(
            stations=tuple(stations),
            timing=CycleTiming(
                period_ms=int(cycle.get("period_ms", 1000)),
                guard_ms=int(cycle.get("guard_ms", 40)),
                slot_ms=int(cycle.get("slot_ms", 150)),
            ),
            arq_timeout_ms=int(arq.get("timeout_ms", 400)),
            arq_max_attempts=int(arq.get("max_attempts", 3)),
            window_cycles=int(health.get("window_cycles", 20)),
            retry_rate_threshold=float(health.get("retry_rate_threshold", 0.20)),
            stale_cycles=int(health.get("stale_cycles", 5)),
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
class _CycleTracker:
    """Per-station bookkeeping for the poll cycle in progress."""

    filled: bool = False
    retried: bool = False
    finalized: bool = False
    retransmissions: int = 0
    poll_sent_ms: int = 0


@dataclass
class _StationRuntime:
    sender: StopAndWaitSender
    health: HealthEvaluator
    reassembler: Reassembler = field(default_factory=Reassembler)
    outbox: deque[tuple[MsgType, bytes, PendingPush | None]] = field(default_factory=deque)
    inflight: PendingPush | None = None
    desired: dict[str, Any] | None = None
    cycle: _CycleTracker = field(default_factory=_CycleTracker)


class Master:
    """Composition root for the poll cycle, health, transport and parameters."""

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
        ordered = sorted(config.stations, key=lambda s: s.slot_index)
        self.scheduler = PollScheduler(
            [s.addr for s in ordered], config.timing, master_addr=config.master_addr
        )
        self.transport = TransportSelector(
            promote_probes=config.promote_probes,
            demote_probes=config.demote_probes,
            dwell_s=config.dwell_s,
        )
        self.params = ParameterDistributor(mirror)
        self.states: dict[int, StationState] = {}
        self._runtime: dict[int, _StationRuntime] = {}
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
                    window_cycles=config.window_cycles,
                    retry_rate_threshold=config.retry_rate_threshold,
                    stale_cycles=config.stale_cycles,
                ),
            )
        self._cycle_active = False
        self._cycle_deadline_ms = 0
        self._next_cycle_ms: int | None = None

    def _log(self, event: str, **fields: Any) -> None:
        if self.logger is not None:
            self.logger.event(event, **fields)

    # -- operator entry points ---------------------------------------------

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
        """Feed a WLAN-reachability probe; a carrier change resets the window."""
        result = self.transport.on_probe(addr, success, now_ms)
        self.states[addr].carrier = result.active
        if result.changed:
            self._runtime[addr].health.reset()
            self._log("carrier_switch", station=addr, carrier=result.active.value)

    # -- state store for the UI --------------------------------------------

    def health_snapshot(self, addr: int) -> HealthSnapshot:
        return self._runtime[addr].health.snapshot

    def config_snapshot(self, addr: int) -> ConfigSnapshot:
        return self.params.snapshot(addr)

    # -- the loop ----------------------------------------------------------

    def step(self, now_ms: int) -> None:
        """Advance one tick: drain incoming frames, service ARQ, run the cycle."""
        self._receive(now_ms)
        self._service_arq(now_ms)
        self._service_cycle(now_ms)

    def _receive(self, now_ms: int) -> None:
        data = self.carrier.recv()
        if not data:
            return
        for frame in self._reader.feed(data):
            self._dispatch(frame, now_ms)

    def _dispatch(self, frame: Frame, now_ms: int) -> None:
        if frame.msg_type is MsgType.BEARING:
            self._on_bearing(frame, now_ms)
        elif frame.msg_type is MsgType.ACK:
            self._on_ack(frame, now_ms)
        elif frame.msg_type is MsgType.PARAM_REPORT:
            self._on_param_report(frame)
        # IDENT and stray POLLs are ignored by the master.

    def _on_bearing(self, frame: Frame, now_ms: int) -> None:
        addr = frame.src
        if not self.scheduler.is_configured(addr):
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
        self.scheduler.record_bearing(addr, now_ms)
        runtime = self._runtime[addr]
        runtime.cycle.filled = True
        self.params.observe_bearing(addr, report.config_version, report.config_crc)

    def _on_ack(self, frame: Frame, now_ms: int) -> None:
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

    def _on_param_report(self, frame: Frame) -> None:
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

    # -- ARQ servicing -----------------------------------------------------

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
                    runtime.cycle.retransmissions += 1
                    self._log("arq_retransmit", station=addr, seq=frame.seq)
                elif event is TxEvent.EXHAUSTED:
                    runtime.inflight = None
                    self._log("arq_exhausted", station=addr)
            # Author a queued delta lazily so it reflects the freshest mirror.
            if not sender.busy and runtime.desired is not None and not runtime.outbox:
                self._author_delta(addr, runtime)
            if not sender.busy and runtime.outbox:
                msg_type, payload, push = runtime.outbox.popleft()
                frame = sender.start(msg_type, payload, now_ms)
                runtime.inflight = push
                self.carrier.send(frame.encode())
                self._log("tx", station=addr, type=msg_type.name, seq=frame.seq)

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

    # -- poll cycle --------------------------------------------------------

    def _service_cycle(self, now_ms: int) -> None:
        if not self._cycle_active:
            if self._next_cycle_ms is None or now_ms >= self._next_cycle_ms:
                self._begin_cycle(now_ms)
            return
        if now_ms >= self._cycle_deadline_ms:
            self._end_cycle(now_ms)

    def _begin_cycle(self, now_ms: int) -> None:
        frame = self.scheduler.start_cycle(now_ms)
        self.carrier.send(frame.encode())
        for runtime in self._runtime.values():
            runtime.cycle = _CycleTracker(poll_sent_ms=now_ms)
        self._cycle_active = True
        span = self.config.timing.guard_ms + len(self.states) * self.config.timing.slot_ms
        self._cycle_deadline_ms = now_ms + span
        self._next_cycle_ms = now_ms + self.config.timing.period_ms
        self._log("poll", cycle_seq=self.scheduler.cycle_seq, jitter_ms=self.scheduler.jitter_ms)

    def _end_cycle(self, now_ms: int) -> None:
        still_open = False
        for addr, runtime in self._runtime.items():
            tracker = runtime.cycle
            if tracker.finalized:
                continue
            if tracker.filled:
                self._finalize_station(addr, runtime, delivered=True, exhausted=False)
            elif not tracker.retried:
                # One unicast retry for the missed station (FR-5.5) before giving up.
                self.carrier.send(self.scheduler.retry_poll(addr, now_ms).encode())
                tracker.retried = True
                tracker.retransmissions += 1
                self._cycle_deadline_ms = now_ms + self.config.timing.slot_ms
                still_open = True
                self._log("poll_retry", station=addr)
            else:
                self._finalize_station(addr, runtime, delivered=False, exhausted=True)
        if not still_open:
            self._cycle_active = False

    def _finalize_station(
        self, addr: int, runtime: _StationRuntime, *, delivered: bool, exhausted: bool
    ) -> None:
        tracker = runtime.cycle
        tracker.finalized = True
        state = self.states[addr]
        rtt = None
        rssi = None
        if delivered and state.last_bearing is not None:
            rtt = float(state.last_bearing.age_ms)
            rssi = float(state.last_bearing.power_dbm)
        runtime.health.add_cycle(
            CycleOutcome(
                delivered=delivered,
                retransmissions=tracker.retransmissions,
                exhausted=exhausted,
                rtt_ms=rtt,
                rssi_dbm=rssi,
            )
        )
        if not delivered:
            self._log("missed_slot", station=addr)
