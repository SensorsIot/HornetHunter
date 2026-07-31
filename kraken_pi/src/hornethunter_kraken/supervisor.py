"""KrakenProxy supervision & autorecovery (FSD §5.5, N4/FR-22.2).

A pure watchdog over the DoA feed / KrakenSDR DSP. When the feed stalls (the DSP is
unreachable or wedged, distinct from a live-but-squelched feed) the supervisor
attempts **bounded, backed-off, escalating** recovery through an injected action —
typically a `systemctl restart` of the KrakenSDR service. Recovery attempts are
counted and logged; once the budget is spent the fault is left **indicated** (the
station goes stale → RED at the master), never silently retried (N4).

The recovery action is injected so the state machine stays hardware-free and
unit-testable; `systemctl_restart` builds the real one.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = ["RecoveryPolicy", "Supervisor", "systemctl_restart"]


@dataclass(frozen=True)
class RecoveryPolicy:
    """Watchdog + recovery bounds (configuration). Seconds."""

    stall_after_s: float = 10.0  # feed unreachable this long → begin recovery
    backoff_s: float = 30.0  # minimum gap between recovery attempts
    max_attempts: int = 3  # bounded budget; then indicate and stop (N4)


@dataclass
class Supervisor:
    """Feed/DSP watchdog with bounded-escalating autorecovery (§5.5)."""

    policy: RecoveryPolicy
    recover: Callable[[], None]
    logger: Any | None = None
    _last_alive_s: float | None = None
    _attempts: int = 0
    _last_attempt_s: float | None = None
    _exhausted: bool = False

    def note_alive(self, now_s: float) -> None:
        """The feed is reachable — reset the watchdog and the recovery budget."""
        self._last_alive_s = now_s
        if self._attempts or self._exhausted:
            self._log("autorecover_cleared", attempts=self._attempts)
        self._attempts = 0
        self._last_attempt_s = None
        self._exhausted = False

    def tick(self, now_s: float) -> None:
        """Called each loop. If the feed has stalled past the threshold, attempt one
        recovery — respecting the backoff and the attempt cap."""
        if self._last_alive_s is None:
            self._last_alive_s = now_s  # first tick establishes the baseline
            return
        if self._exhausted or now_s - self._last_alive_s < self.policy.stall_after_s:
            return
        if (
            self._last_attempt_s is not None
            and now_s - self._last_attempt_s < self.policy.backoff_s
        ):
            return  # within the backoff window
        self._attempts += 1
        self._last_attempt_s = now_s
        self._log("autorecover_attempt", attempt=self._attempts)
        try:
            self.recover()
        except Exception as exc:  # noqa: BLE001 - a failed recovery must not crash the loop
            self._log("autorecover_error", error=str(exc))
        if self._attempts >= self.policy.max_attempts:
            self._exhausted = True
            self._log("autorecover_exhausted", attempts=self._attempts)

    @property
    def indicated(self) -> bool:
        """True once the fault has outlived its recovery budget (left for a human, N4)."""
        return self._exhausted

    def _log(self, event: str, **fields: Any) -> None:
        if self.logger is not None:
            self.logger.event(event, **fields)


def systemctl_restart(*services: str) -> Callable[[], None]:
    """Build a recovery action that restarts one or more systemd services.

    Runs `sudo systemctl restart <services>`; a non-zero exit raises so the
    supervisor logs it. Kept out of the state machine so tests inject a fake."""

    def _action() -> None:
        subprocess.run(
            ["sudo", "systemctl", "restart", *services],
            check=True,
            capture_output=True,
            timeout=30,
        )

    return _action
