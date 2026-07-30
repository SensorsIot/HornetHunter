"""Stop-and-wait ARQ (FSD §10.5).

One outstanding frame per direction per station. The medium is half-duplex with a
single master, so there is no window to exploit. Time is injected as a millisecond
clock value on every call, so the state machine is pure and testable at the host
tier with no real timers (NFR-10.1).

The carrier discards frames that fail their PHY CRC (§15.3): the link loses frames
but never corrupts them, so sequence + ACK + retransmit is sufficient.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from .frame import Frame, MsgType
from .protocol import AckPayload


class TxEvent(Enum):
    """Outcome of `StopAndWaitSender.on_timeout`."""

    RETRANSMIT = auto()
    EXHAUSTED = auto()


def make_ack(
    dest: int,
    src: int,
    acked_seq: int,
    *,
    config_version: int = 0,
    config_crc: int = 0,
    status: int = 0,
) -> Frame:
    """Build an ACK frame carrying config state (FSD §7.5, §10.3)."""
    payload = AckPayload(acked_seq, config_version, config_crc, status).encode()
    return Frame(MsgType.ACK, dest=dest, src=src, seq=acked_seq, payload=payload)


@dataclass
class StopAndWaitSender:
    """Drives one transaction at a time toward a single peer.

    Usage: `start()` to send, feed incoming ACKs to `on_ack()`, and call
    `on_timeout()` when `deadline_ms` has passed to get a retransmit or exhaustion.
    """

    dest: int
    src: int
    timeout_ms: int = 400
    max_attempts: int = 3
    _seq: int = 0
    _pending: Frame | None = None
    _attempts: int = 0
    deadline_ms: int = 0

    @property
    def busy(self) -> bool:
        return self._pending is not None

    @property
    def attempts(self) -> int:
        return self._attempts

    def start(self, msg_type: MsgType, payload: bytes, now_ms: int) -> Frame:
        """Begin a transaction and return the frame to transmit."""
        if self._pending is not None:
            raise RuntimeError("sender busy: one outstanding frame per peer (§10.5)")
        frame = Frame(msg_type, dest=self.dest, src=self.src, seq=self._seq, payload=payload)
        self._pending = frame
        self._attempts = 1
        self.deadline_ms = now_ms + self.timeout_ms
        return frame

    def on_ack(self, acked_seq: int) -> bool:
        """Return True and clear the transaction when the ACK matches the pending
        sequence; ignore mismatched or duplicate ACKs."""
        if self._pending is not None and acked_seq == self._pending.seq:
            self._pending = None
            self._seq = (self._seq + 1) & 0xFF
            return True
        return False

    def on_timeout(self, now_ms: int) -> tuple[TxEvent, Frame | None]:
        """Advance the retransmission timer. Call when `now_ms >= deadline_ms`."""
        if self._pending is None:
            raise RuntimeError("no transaction in progress")
        if self._attempts >= self.max_attempts:
            failed = self._pending
            self._pending = None  # abandon; seq is NOT advanced on failure
            return TxEvent.EXHAUSTED, failed
        self._attempts += 1
        self.deadline_ms = now_ms + self.timeout_ms
        return TxEvent.RETRANSMIT, self._pending


@dataclass
class DedupReceiver:
    """Suppresses duplicate deliveries by sequence number (FR-10.7).

    A retransmission whose ACK was lost arrives with the same sequence number as
    the last accepted frame from that source; `accept()` reports it as a duplicate
    so the caller re-ACKs but applies the payload only once.
    """

    _last_seq: dict[int, int] = field(default_factory=dict)

    def accept(self, frame: Frame) -> bool:
        """Return True when `frame` is new and should be applied; False for a
        duplicate (which must still be acknowledged)."""
        if self._last_seq.get(frame.src) == frame.seq:
            return False
        self._last_seq[frame.src] = frame.seq
        return True
