"""Link health evaluator (FSD §8).

Converts link behaviour into a single per-station colour and into metrics for
audit. Health is derived **exclusively** from retransmission and delivery outcomes
over a rolling window; RSSI is exposed for display but never contributes to the
indicator (NFR-8.1). Configuration divergence (§7.5) is a separate axis and is not
represented here (FR-8.6) — it lives in `param_dist`.

The evaluator is pure: it is fed one constructed `CycleOutcome` per poll cycle and
its colour is asserted directly at the host tier (§24.1, AT-6).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

WINDOW_CYCLES_DEFAULT = 20
RETRY_RATE_THRESHOLD_DEFAULT = 0.20
STALE_CYCLES_DEFAULT = 5


class HealthState(StrEnum):
    """Per-station link health (§8.3). `CONFIG_DIVERGED` is deliberately absent —
    it is an independent axis (FR-8.6)."""

    GREEN = "green"
    ORANGE = "orange"
    RED = "red"
    LOST = "lost"


@dataclass(frozen=True)
class CycleOutcome:
    """The retransmission/delivery result of one poll cycle for one station.

    `retransmissions` is the number of retransmits the cycle needed (0 = first
    try); `exhausted` marks ARQ giving up (§10.5); `delivered` is whether a valid
    BEARING was ultimately obtained. `rtt_ms` and `rssi_dbm` are carried for
    display only (FR-8.7).
    """

    delivered: bool
    retransmissions: int = 0
    exhausted: bool = False
    rtt_ms: float | None = None
    rssi_dbm: float | None = None


@dataclass(frozen=True)
class HealthSnapshot:
    """Everything the UI and the log need for one station (FR-8.7, §8.5)."""

    state: HealthState
    warming_up: bool
    retry_count: int
    retry_rate: float
    consecutive_misses: int
    rtt_ms: float | None
    last_rssi_dbm: float | None
    window_len: int
    window_cycles: int


@dataclass
class HealthEvaluator:
    """Rolling-window health for one station.

    Thresholds and window length are runtime configuration (NFR-8.2) and may be
    reassigned on the instance. The window resets on any carrier change (FR-8.5).
    """

    window_cycles: int = WINDOW_CYCLES_DEFAULT
    retry_rate_threshold: float = RETRY_RATE_THRESHOLD_DEFAULT
    stale_cycles: int = STALE_CYCLES_DEFAULT
    carriers_down: bool = False
    _cycles: deque[CycleOutcome] = field(default_factory=deque)
    _last_rtt_ms: float | None = None
    _last_rssi_dbm: float | None = None

    def reset(self) -> None:
        """Clear the window (FR-8.5, §6.4). RTT/RSSI display values are retained."""
        self._cycles.clear()

    def add_cycle(self, outcome: CycleOutcome) -> None:
        """Record one cycle, trimming to the (possibly reconfigured) window length."""
        self._cycles.append(outcome)
        while len(self._cycles) > self.window_cycles:
            self._cycles.popleft()
        if outcome.rtt_ms is not None:
            self._last_rtt_ms = outcome.rtt_ms
        if outcome.rssi_dbm is not None:
            self._last_rssi_dbm = outcome.rssi_dbm

    def _consecutive_misses(self) -> int:
        misses = 0
        for outcome in reversed(self._cycles):
            if outcome.delivered:
                break
            misses += 1
        return misses

    def _retry_count(self) -> int:
        return sum(1 for outcome in self._cycles if outcome.retransmissions > 0)

    @property
    def snapshot(self) -> HealthSnapshot:
        """Evaluate the current window into a colour and metrics."""
        window_len = len(self._cycles)
        retry_count = self._retry_count()
        retry_rate = retry_count / window_len if window_len else 0.0
        misses = self._consecutive_misses()
        warming_up = window_len < self.window_cycles

        state = self._state(window_len, retry_count, retry_rate, misses, warming_up)
        return HealthSnapshot(
            state=state,
            warming_up=warming_up,
            retry_count=retry_count,
            retry_rate=retry_rate,
            consecutive_misses=misses,
            rtt_ms=self._last_rtt_ms,
            last_rssi_dbm=self._last_rssi_dbm,
            window_len=window_len,
            window_cycles=self.window_cycles,
        )

    def _state(
        self,
        window_len: int,
        retry_count: int,
        retry_rate: float,
        misses: int,
        warming_up: bool,
    ) -> HealthState:
        # Until the window fills after a start or reset, thresholds are not applied
        # and the state is GREEN with a warming-up qualifier (§8.5).
        if warming_up:
            return HealthState.GREEN

        delivered_any = any(outcome.delivered for outcome in self._cycles)
        if self.carriers_down and not delivered_any:
            return HealthState.LOST

        exhausted = any(outcome.exhausted for outcome in self._cycles)
        if misses >= self.stale_cycles or exhausted or retry_rate > self.retry_rate_threshold:
            return HealthState.RED
        if retry_count > 0:
            return HealthState.ORANGE
        return HealthState.GREEN
