"""TDMA superframe scheduling (FSD §5, §6).

Pure logic — no I/O, no clock. The Management Pi (master) owns the schedule and
builds each beacon; a station reads its slots from the beacon and times them off the
beacon's arrival, so **no absolute clock is needed** (the USB-GPS PPS is unusable,
§5). Timing-zero is always "when the beacon was heard".

Superframe layout (frame slots), FSD §5.2::

    | 0: BEACON | 1: JOIN/contention | 2..N-1: data slots |

Data slots are mapped to live stations in ascending-number order, compacted so an
absent number reserves nothing, round-robin up to a per-station cap — so a lightly
loaded frame runs each station faster and the rate downsamples only as stations join
(FR-5.6).
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Superframe", "Scheduler", "assign_slots", "join_offset_ms"]


@dataclass(frozen=True)
class Superframe:
    """Fixed superframe geometry (configuration, FSD §5.2, NFR-5.2). Milliseconds."""

    period_ms: int = 1000
    slot_ms: int = 125
    guard_ms: int = 25
    max_slots_per_station: int = 2

    @property
    def n_slots(self) -> int:
        return max(2, self.period_ms // self.slot_ms)

    @property
    def n_data_slots(self) -> int:
        # slot 0 = beacon, slot 1 = join/contention, the rest carry bearings.
        return max(0, self.n_slots - 2)

    def data_slot_window(self, data_slot: int) -> tuple[int, int]:
        """(start, end) ms of a data slot's transmit window, relative to the beacon
        arrival (t0). The guard trims both ends so tx-start jitter cannot spill into
        a neighbour (§5.2)."""
        frame_slot = 2 + data_slot  # after beacon(0) + join(1)
        start = frame_slot * self.slot_ms + self.guard_ms
        end = (frame_slot + 1) * self.slot_ms - self.guard_ms
        return start, end

    def join_window(self) -> tuple[int, int]:
        """(start, end) ms of the join/contention window, relative to t0 (§5.3)."""
        return self.slot_ms + self.guard_ms, 2 * self.slot_ms - self.guard_ms


def assign_slots(live: list[int], n_data_slots: int, max_per_station: int) -> list[int]:
    """Map each data slot to a live station number (0 = idle), FSD §5.2 / FR-5.6.

    Live stations are taken in ascending order (compacted). Round-robin, capped at
    `max_per_station`, so few stations run faster and the per-station rate only
    downsamples as the frame fills."""
    result = [0] * max(0, n_data_slots)
    ordered = sorted(set(s for s in live if s > 0))
    if not ordered or n_data_slots <= 0:
        return result
    counts = {s: 0 for s in ordered}
    idx = 0
    while idx < n_data_slots:
        progressed = False
        for s in ordered:
            if idx >= n_data_slots:
                break
            if counts[s] < max_per_station:
                result[idx] = s
                counts[s] += 1
                idx += 1
                progressed = True
        if not progressed:  # every live station is at its cap
            break
    return result


def join_offset_ms(superframe: Superframe, rng_val: float) -> int:
    """A transmit offset within the join window for contention backoff (FR-5.5).

    `rng_val` in [0, 1) is supplied by the caller (kept out of here so the function
    stays pure and testable); the station draws it per attempt."""
    start, end = superframe.join_window()
    span = max(1, end - start)
    return start + int(max(0.0, min(0.999, rng_val)) * span)


@dataclass
class Scheduler:
    """The master's live-set and slot allocation (FSD §5, §6).

    Pure with respect to time: the caller passes monotonic milliseconds; there is no
    clock inside. `saw()` on every frame heard from a station, `beacon()` once per
    superframe to build the outgoing beacon (seq, config_target, slot-map)."""

    superframe: Superframe = field(default_factory=Superframe)
    staleness_ms: int = 3000
    _last_seen: dict[int, int] = field(default_factory=dict)
    _seq: int = 0

    def saw(self, station: int, now_ms: int) -> None:
        """Record a frame (bearing, heartbeat or join) heard from a station."""
        if station > 0:
            self._last_seen[station] = now_ms

    def retire(self, station: int) -> None:
        """Drop a station immediately — operator retire (FR-14.12) or departure."""
        self._last_seen.pop(station, None)

    def live(self, now_ms: int) -> list[int]:
        """Stations heard within the staleness window, ascending (§5.3)."""
        return sorted(
            s for s, t in self._last_seen.items() if now_ms - t <= self.staleness_ms
        )

    def slot_map(self, now_ms: int) -> list[int]:
        """The current data-slot → station assignment (compacted, adaptive)."""
        return assign_slots(
            self.live(now_ms),
            self.superframe.n_data_slots,
            self.superframe.max_slots_per_station,
        )

    def beacon(self, now_ms: int, config_target: int = 0) -> tuple[int, int, tuple[int, ...]]:
        """Build the next beacon and advance the superframe sequence.

        Returns `(seq, config_target, slots)` — feed straight into `BeaconPayload`."""
        self._seq = (self._seq + 1) & 0xFFFF
        return self._seq, config_target & 0xFF, tuple(self.slot_map(now_ms))
