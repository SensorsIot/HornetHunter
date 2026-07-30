"""Poll cycle scheduler (FSD §5).

The Management Pi is the master and owns the schedule on the shared medium
(§2.1): stations never initiate, they only answer a POLL in their assigned slot.
Each cycle the scheduler broadcasts one POLL naming the cycle and the expected
repliers, then tracks per-slot deadlines against an injected millisecond clock so
the logic is pure and host-testable (§24.1).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hornethunter_shared.frame import Frame, MsgType
from hornethunter_shared.protocol import PollPayload

BROADCAST_ADDR = 0xFF


@dataclass(frozen=True)
class CycleTiming:
    """Cycle geometry (§5.3), all operator configuration with no policy limit
    imposed by the scheduler (FR-5.3)."""

    period_ms: int
    guard_ms: int
    slot_ms: int


@dataclass(frozen=True)
class SlotResult:
    """Outcome of accepting a BEARING against the schedule."""

    known: bool  # the source is a configured station (FR-18.3)
    on_time: bool  # arrived within its slot
    late: bool  # arrived after its slot deadline (§5.4 `late`)


class PollScheduler:
    """Owns the poll schedule for a fixed set of stations.

    Station addresses are HH-Link addresses (§18.2); the master broadcasts to
    `BROADCAST_ADDR`. Slot indices are assigned by position in `station_addrs`
    (FR-5.2) and are stable for the life of the scheduler.
    """

    def __init__(
        self,
        station_addrs: Sequence[int],
        timing: CycleTiming,
        *,
        master_addr: int = BROADCAST_ADDR,
        broadcast_addr: int = BROADCAST_ADDR,
    ) -> None:
        self.timing = timing
        self.master_addr = master_addr
        self.broadcast_addr = broadcast_addr
        self._slot_of: dict[int, int] = {addr: i for i, addr in enumerate(station_addrs)}
        self._cycle_seq = 0
        self.cycle_start_ms: int | None = None
        self._expected_start_ms: int | None = None
        self.jitter_ms = 0
        self._filled: set[int] = set()

    @property
    def stations(self) -> tuple[int, ...]:
        return tuple(self._slot_of)

    @property
    def cycle_seq(self) -> int:
        return self._cycle_seq

    def is_configured(self, addr: int) -> bool:
        return addr in self._slot_of

    def slot_index(self, addr: int) -> int:
        return self._slot_of[addr]

    def expected_bitmap(self) -> int:
        """Bitmap of every configured station's slot, for a broadcast POLL."""
        bitmap = 0
        for slot in self._slot_of.values():
            bitmap |= 1 << slot
        return bitmap

    def next_start_ms(self) -> int | None:
        """When the next cycle is due; None before the first cycle starts."""
        return self._expected_start_ms

    def start_cycle(self, now_ms: int) -> Frame:
        """Begin a cycle and return the broadcast POLL to transmit (FR-5.1).

        Records the cycle-start jitter against the schedule (NFR-5.1) so drift is
        never silent, and resets the per-slot fill tracking.
        """
        if self._expected_start_ms is not None:
            self.jitter_ms = now_ms - self._expected_start_ms
        self._cycle_seq = (self._cycle_seq + 1) & 0xFFFF
        self.cycle_start_ms = now_ms
        self._expected_start_ms = now_ms + self.timing.period_ms
        self._filled = set()
        payload = PollPayload(self._cycle_seq, self.timing.slot_ms, self.expected_bitmap())
        return Frame(
            MsgType.POLL,
            dest=self.broadcast_addr,
            src=self.master_addr,
            seq=self._cycle_seq & 0xFF,
            payload=payload.encode(),
        )

    def slot_deadline(self, addr: int) -> int:
        """Absolute deadline by which `addr`'s BEARING must have arrived."""
        if self.cycle_start_ms is None:
            raise RuntimeError("no cycle in progress")
        slot = self._slot_of[addr]
        return self.cycle_start_ms + self.timing.guard_ms + (slot + 1) * self.timing.slot_ms

    def record_bearing(self, addr: int, now_ms: int) -> SlotResult:
        """Register a BEARING from `addr`. A frame from an unconfigured station is
        rejected (FR-18.3); one outside its slot is accepted but flagged `late`."""
        if addr not in self._slot_of:
            return SlotResult(known=False, on_time=False, late=False)
        self._filled.add(addr)
        late = now_ms > self.slot_deadline(addr)
        return SlotResult(known=True, on_time=not late, late=late)

    def is_filled(self, addr: int) -> bool:
        return addr in self._filled

    def missed_slots(self, now_ms: int) -> list[int]:
        """Configured stations whose slot deadline has passed unfilled (FR-5.4)."""
        return [
            addr
            for addr in self._slot_of
            if addr not in self._filled and now_ms > self.slot_deadline(addr)
        ]

    def retry_poll(self, addr: int, now_ms: int) -> Frame:
        """A unicast POLL retrying one missed station rather than re-broadcasting
        (FR-5.5)."""
        if addr not in self._slot_of:
            raise KeyError(f"station {addr:#04x} is not configured")
        payload = PollPayload(self._cycle_seq, self.timing.slot_ms, 1 << self._slot_of[addr])
        return Frame(
            MsgType.POLL,
            dest=addr,
            src=self.master_addr,
            seq=self._cycle_seq & 0xFF,
            payload=payload.encode(),
        )
