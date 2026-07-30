"""Transport selector (FSD §6).

Chooses the carrier — WLAN or LoRa — independently for each station (FR-6.1). The
selector is fed probe results plus a millisecond clock and reports the active
carrier per station (FR-6.6); it holds no sockets and does no I/O, so it is pure
and host-testable. Promotion to WLAN is hysteretic (`promote_probes` consecutive
successes), demotion is faster (`demote_probes` consecutive failures), and a
station dwells on a carrier for `dwell_s` after a switch before another is allowed
(FR-6.3, FR-6.4). An operator pin disables automatic selection for a station
(FR-6.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class CarrierKind(StrEnum):
    """The two byte carriers a station may run over (§15.2). Both run the identical
    frame protocol (§6.4)."""

    WLAN = "wlan"
    LORA = "lora"


@dataclass(frozen=True)
class ProbeResult:
    """Return value of `on_probe`: the active carrier and whether it just changed."""

    active: CarrierKind
    changed: bool


@dataclass
class _StationTransport:
    active: CarrierKind = CarrierKind.LORA
    pin: CarrierKind | None = None
    successes: int = 0
    failures: int = 0
    last_switch_ms: int | None = None


@dataclass
class TransportSelector:
    """Per-station carrier state machine.

    Defaults follow §6.3. A station starts on LoRa — polling always continues on
    LoRa (§6.5) — and is promoted to WLAN only once it has proven reachable.
    """

    promote_probes: int = 3
    demote_probes: int = 2
    dwell_s: float = 30.0
    _stations: dict[int, _StationTransport] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.demote_probes < self.promote_probes:
            raise ValueError("demote_probes must be less than promote_probes (FR-6.3)")

    @property
    def _dwell_ms(self) -> int:
        return int(self.dwell_s * 1000)

    def _station(self, addr: int) -> _StationTransport:
        st = self._stations.get(addr)
        if st is None:
            st = _StationTransport()
            self._stations[addr] = st
        return st

    def active(self, addr: int) -> CarrierKind:
        """The carrier currently selected for `addr`."""
        st = self._station(addr)
        return st.pin if st.pin is not None else st.active

    def pin(self, addr: int) -> CarrierKind | None:
        """The pinned carrier for `addr`, or None when automatic (FR-6.5)."""
        return self._station(addr).pin

    def set_pin(self, addr: int, carrier: CarrierKind | None) -> None:
        """Pin `addr` to a carrier, disabling auto-selection, or None to re-enable."""
        self._station(addr).pin = carrier

    def _dwell_ok(self, st: _StationTransport, now_ms: int) -> bool:
        return st.last_switch_ms is None or now_ms - st.last_switch_ms >= self._dwell_ms

    def on_probe(self, addr: int, success: bool, now_ms: int) -> ProbeResult:
        """Feed one WLAN-reachability probe result and get the resulting carrier.

        A pin freezes the carrier and consumes the probe without switching. Under
        automatic selection the consecutive-success / consecutive-failure counters
        drive a hysteretic, dwell-limited switch.
        """
        st = self._station(addr)
        if success:
            st.successes += 1
            st.failures = 0
        else:
            st.failures += 1
            st.successes = 0

        if st.pin is not None:
            return ProbeResult(active=st.pin, changed=False)

        changed = False
        if (
            st.active is CarrierKind.LORA
            and st.successes >= self.promote_probes
            and self._dwell_ok(st, now_ms)
        ):
            st.active = CarrierKind.WLAN
            st.successes = 0
            st.failures = 0
            st.last_switch_ms = now_ms
            changed = True
        elif (
            st.active is CarrierKind.WLAN
            and st.failures >= self.demote_probes
            and self._dwell_ok(st, now_ms)
        ):
            st.active = CarrierKind.LORA
            st.successes = 0
            st.failures = 0
            st.last_switch_ms = now_ms
            changed = True

        return ProbeResult(active=st.active, changed=changed)
