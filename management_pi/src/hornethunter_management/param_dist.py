"""Parameter distribution and configuration-integrity state machine (FSD §7).

Computes deltas (changed fields only, FR-7.1) against the per-station mirror,
assigns the next `config_version` (FR-7.3) and the expected canonical CRC (FR-7.4),
and drives the divergence response (FR-7.7): on a version/CRC mismatch it raises
`CONFIG_DIVERGED`, performs **exactly one** automatic full-set push and sets a
sticky "resynced" marker; if the mismatch survives that push it latches and
requires operator action.

The full-set encoding serialises only VFO slots below `active_vfos` (FR-7.10). The
divergence machine is pure over injected observations and is host-tested (AT-11).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from hornethunter_shared.registry import (
    BY_KEY,
    FIELD_REGISTRY,
    canonical_crc,
    encode_delta,
)

from .mirror import ConfigMirror

_VFO_SLOT = re.compile(r"^vfo_.+_(\d+)$")

# ACK status flags (§8.7, §14.5). Mirrors the KrakenProxy's ACK payload.
STATUS_KRAKEN_DOWN = 1 << 0
STATUS_READ_ONLY = 1 << 1


class ConfigState(StrEnum):
    """Configuration-integrity axis, independent of link health (FR-8.6)."""

    UNKNOWN = "unknown"  # no baseline; a manual full read is required (§7.7)
    IN_SYNC = "in_sync"
    PENDING = "pending"  # a push is in flight, not yet confirmed
    DIVERGED = "diverged"  # mismatch; the one automatic full push is in flight
    LATCHED = "latched"  # mismatch persisted after the auto push; operator action
    KRAKEN_DOWN = "kraken_down"  # read-back unavailable; CRC not asserted


class NoBaselineError(RuntimeError):
    """Raised when a delta is requested for a station with no mirror baseline."""


@dataclass(frozen=True)
class PendingPush:
    """A push to hand to the transport: a delta or a full set, with the target
    version and the CRC the station is expected to report back."""

    kind: str  # "delta" or "full"
    payload: bytes
    version: int
    expected_crc: int
    settings: dict[str, Any]  # the full settings the station should hold on success
    changes: dict[str, Any]  # for logging (FR-20.4)


@dataclass(frozen=True)
class PushResult:
    """The outcome of feeding an ACK/observation to the distributor."""

    state: ConfigState
    committed: bool = False  # the push was accepted into the mirror
    auto_push: PendingPush | None = None  # a full push the caller must now send


@dataclass
class _Station:
    state: ConfigState = ConfigState.UNKNOWN
    pending: PendingPush | None = None
    resynced: bool = False  # sticky marker: the one auto full push has been issued
    latched: bool = False


@dataclass(frozen=True)
class ConfigSnapshot:
    """Configuration state for the UI (§14.10), separate from link health."""

    state: ConfigState
    config_version: int
    expected_crc: int
    resynced: bool
    latched: bool


def _next_version(current: int) -> int:
    return (current + 1) & 0xFF


def encode_full(settings: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """Encode a full set, dropping VFO slots at or above `active_vfos` (FR-7.10).

    Returns the wire bytes and the exact field map they encode.
    """
    active = int(settings.get("active_vfos", 0))
    changes: dict[str, Any] = {}
    for spec in FIELD_REGISTRY:
        if spec.key not in settings:
            continue
        match = _VFO_SLOT.match(spec.key)
        if match is not None and int(match.group(1)) >= active:
            continue
        changes[spec.key] = settings[spec.key]
    return encode_delta(changes), changes


class ParameterDistributor:
    """Owns the delta mechanism and the divergence response for every station."""

    def __init__(self, mirror: ConfigMirror) -> None:
        self.mirror = mirror
        self._stations: dict[int, _Station] = {}

    def _station(self, addr: int) -> _Station:
        st = self._stations.get(addr)
        if st is None:
            initial = ConfigState.IN_SYNC if self.mirror.has(addr) else ConfigState.UNKNOWN
            st = _Station(state=initial)
            self._stations[addr] = st
        return st

    def snapshot(self, addr: int) -> ConfigSnapshot:
        st = self._station(addr)
        current = self.mirror.current(addr)
        expected_crc = canonical_crc(current) if current is not None else 0
        return ConfigSnapshot(
            state=st.state,
            config_version=self.mirror.version(addr),
            expected_crc=expected_crc,
            resynced=st.resynced,
            latched=st.latched,
        )

    # -- authoring pushes ---------------------------------------------------

    def prepare_delta(self, addr: int, desired: dict[str, Any]) -> PendingPush | None:
        """Compute a delta of only the changed fields (FR-7.1) against the mirror.

        Returns None when nothing changed. Raises `NoBaselineError` when the station
        has no mirror entry — a manual full read must seed it first (§7.7).
        """
        baseline = self.mirror.current(addr)
        if baseline is None:
            raise NoBaselineError(f"station {addr:#04x} has no baseline; do a full read first")
        changes = {
            key: value
            for key, value in desired.items()
            if key in BY_KEY and baseline.get(key) != value
        }
        if not changes:
            return None
        merged = {**baseline, **changes}
        version = _next_version(self.mirror.version(addr))
        push = PendingPush(
            kind="delta",
            payload=encode_delta(changes),
            version=version,
            expected_crc=canonical_crc(merged),
            settings=merged,
            changes=changes,
        )
        st = self._station(addr)
        st.pending = push
        st.state = ConfigState.PENDING
        return push

    def prepare_full_push(self, addr: int) -> PendingPush | None:
        """Explicit manual full-set push of the mirror's held config (FR-7.8)."""
        current = self.mirror.current(addr)
        if current is None:
            return None
        payload, changes = encode_full(current)
        push = PendingPush(
            kind="full",
            payload=payload,
            version=self.mirror.version(addr),
            expected_crc=canonical_crc(current),
            settings=current,
            changes=changes,
        )
        st = self._station(addr)
        st.pending = push
        st.state = ConfigState.PENDING
        return push

    def on_full_report(self, addr: int, settings: dict[str, Any]) -> None:
        """Seed the mirror from a full-set read (FR-7.8), establishing the baseline
        for future deltas (§7.7 first contact)."""
        if self.mirror.has(addr):
            self.mirror.accept(addr, settings, self.mirror.version(addr))
        else:
            self.mirror.seed(addr, settings, version=0)
        st = self._station(addr)
        st.state = ConfigState.IN_SYNC
        st.resynced = False
        st.latched = False

    def clear_latch(self, addr: int) -> None:
        """Operator action clearing a latched divergence (§7.7)."""
        st = self._station(addr)
        st.latched = False
        st.resynced = False
        st.state = ConfigState.IN_SYNC if self.mirror.has(addr) else ConfigState.UNKNOWN

    # -- confirmation & continuous verification -----------------------------

    def on_ack(
        self, addr: int, observed_version: int, observed_crc: int, *, status: int = 0
    ) -> PushResult:
        """Handle the ACK to the current pending push (§7.5).

        A matching CRC commits the push into the mirror. A mismatch triggers the
        one automatic full-set push (or latches if that push already ran).
        """
        st = self._station(addr)
        push = st.pending
        if push is None:
            return self.observe_bearing(addr, observed_version, observed_crc)

        if status & STATUS_KRAKEN_DOWN:
            # Read-back unavailable: the CRC is not asserted, so this is not a
            # divergence (§7.7). The change stays un-applied for a later retry.
            st.pending = None
            st.state = ConfigState.KRAKEN_DOWN
            return PushResult(state=ConfigState.KRAKEN_DOWN)

        # The canonical CRC is the end-to-end content proof (§7.4/§7.5). The
        # station's config_version is its own counter and is not transmitted the
        # master's version to echo, so divergence keys on the CRC alone; the
        # master's version is its own mirror bookkeeping, advanced on commit.
        if observed_crc == push.expected_crc:
            self.mirror.accept(addr, push.settings, push.version)
            st.pending = None
            st.state = ConfigState.IN_SYNC
            if push.kind == "full":
                st.resynced = False  # the divergence episode is resolved
            return PushResult(state=ConfigState.IN_SYNC, committed=True)

        return self._on_mismatch(addr, st, push.settings, push.version, push.expected_crc)

    def observe_bearing(self, addr: int, observed_version: int, observed_crc: int) -> PushResult:
        """Continuous re-verification from the CRC carried on every BEARING (§7.5).

        A station changed locally is caught within one cycle. With no baseline this
        is a no-op beyond reporting UNKNOWN.
        """
        st = self._station(addr)
        current = self.mirror.current(addr)
        if current is None:
            st.state = ConfigState.UNKNOWN
            return PushResult(state=ConfigState.UNKNOWN)
        if st.pending is not None:
            return PushResult(state=st.state)

        expected_crc = canonical_crc(current)
        if observed_crc == expected_crc:
            if not st.latched:
                st.state = ConfigState.IN_SYNC
                st.resynced = False
            return PushResult(state=st.state)

        return self._on_mismatch(addr, st, current, self.mirror.version(addr), expected_crc)

    def _on_mismatch(
        self,
        addr: int,
        st: _Station,
        settings: dict[str, Any],
        version: int,
        expected_crc: int,
    ) -> PushResult:
        if st.latched:
            return PushResult(state=ConfigState.LATCHED)
        if not st.resynced:
            # First divergence for this episode: raise CONFIG_DIVERGED and perform
            # exactly one automatic full-set push (FR-7.7).
            payload, changes = encode_full(settings)
            auto = PendingPush(
                kind="full",
                payload=payload,
                version=version,
                expected_crc=expected_crc,
                settings=settings,
                changes=changes,
            )
            st.pending = auto
            st.resynced = True
            st.state = ConfigState.DIVERGED
            return PushResult(state=ConfigState.DIVERGED, auto_push=auto)
        # The auto push already ran and the mismatch persists: latch (§7.7).
        st.pending = None
        st.latched = True
        st.state = ConfigState.LATCHED
        return PushResult(state=ConfigState.LATCHED)
