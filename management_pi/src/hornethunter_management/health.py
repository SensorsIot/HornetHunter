"""Link health evaluator (FSD §8).

Health asks one question per station: *are fresh bearings still arriving?* Because
bearings stream unacknowledged (§5), there are no retransmission counts — liveness
is **staleness**. A station is `RED` when no BEARING has arrived for longer than
`staleness_threshold_s`, `GREEN` while bearings are fresh, and optionally `ORANGE`
when bearings still arrive but the measured rate over a rolling window falls below
`orange_rate_fraction` of the expected rate (elevated loss, FR-8.4). RSSI is exposed
for display but never contributes (NFR-8.1). Configuration divergence (§7.5) is a
separate axis (FR-8.6) and lives in `param_dist`.

The evaluator is pure over an injected clock: it is fed bearing arrivals and its
colour is asserted directly at the host tier (§24.1, AT-6).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

STALENESS_THRESHOLD_S_DEFAULT = 1.0
RATE_WINDOW_S_DEFAULT = 10.0
EXPECTED_RATE_HZ_DEFAULT = 2.3  # KrakenSDR DoA cadence (§12, ~437 ms/frame)
ORANGE_RATE_FRACTION_DEFAULT = 0.5


class HealthState(StrEnum):
    """Per-station link liveness (§8.3). `CONFIG_DIVERGED` is deliberately absent —
    it is an independent axis (FR-8.6)."""

    GREEN = "green"
    ORANGE = "orange"
    RED = "red"


@dataclass(frozen=True)
class HealthSnapshot:
    """Everything the UI and the log need for one station (FR-8.6, §8.5)."""

    state: HealthState
    time_since_last_s: float | None  # None until the first bearing arrives
    rate_hz: float
    last_rssi_dbm: float | None


@dataclass
class HealthEvaluator:
    """Staleness-and-rate health for one station.

    Threshold and window are runtime configuration (NFR-8.2) and may be reassigned
    on the instance. Fed `record_bearing(now_s)` on each arrival and evaluated with
    `snapshot(now_s)`; both take the same monotonic seconds clock.
    """

    staleness_threshold_s: float = STALENESS_THRESHOLD_S_DEFAULT
    rate_window_s: float = RATE_WINDOW_S_DEFAULT
    expected_rate_hz: float = EXPECTED_RATE_HZ_DEFAULT
    orange_rate_fraction: float = ORANGE_RATE_FRACTION_DEFAULT
    _arrivals: deque[float] = field(default_factory=deque)
    _first_s: float | None = None
    _last_s: float | None = None
    _last_rssi_dbm: float | None = None

    def record_bearing(self, now_s: float, *, rssi_dbm: float | None = None) -> None:
        """Record one BEARING arrival (§5). RSSI is retained for display only."""
        if self._first_s is None:
            self._first_s = now_s
        self._last_s = now_s
        self._arrivals.append(now_s)
        if rssi_dbm is not None:
            self._last_rssi_dbm = rssi_dbm
        self._trim(now_s)

    def _trim(self, now_s: float) -> None:
        cutoff = now_s - self.rate_window_s
        while self._arrivals and self._arrivals[0] < cutoff:
            self._arrivals.popleft()

    def snapshot(self, now_s: float) -> HealthSnapshot:
        """Evaluate liveness at `now_s` into a colour and metrics."""
        self._trim(now_s)
        since = None if self._last_s is None else now_s - self._last_s
        rate = len(self._arrivals) / self.rate_window_s if self.rate_window_s else 0.0
        # The rate is only meaningful once a full window has elapsed since the first
        # bearing; before that, a fresh stream is GREEN and never flagged ORANGE.
        established = self._first_s is not None and (now_s - self._first_s) >= self.rate_window_s
        return HealthSnapshot(
            state=self._state(since, rate, established),
            time_since_last_s=since,
            rate_hz=rate,
            last_rssi_dbm=self._last_rssi_dbm,
        )

    def _state(self, since: float | None, rate: float, established: bool) -> HealthState:
        # No bearing ever, or none within the threshold: the stream is stale (§8.3).
        if since is None or since > self.staleness_threshold_s:
            return HealthState.RED
        # Receiving, but below the expected rate over an established window: elevated
        # loss, early warning short of stale.
        if established and rate < self.orange_rate_fraction * self.expected_rate_hz:
            return HealthState.ORANGE
        return HealthState.GREEN
