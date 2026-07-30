"""LoRa DTU provisioning over the SX1262 AT command set (FSD §11).

On startup the agent reads the DTU's current parameters, writes only the ones that
differ (FR-11.1, FR-11.2), verifies each written parameter by read-back except the
write-only `AT+KEY` (FR-11.3), and **guarantees `AT+EXIT` on every exit path,
including on error** (FR-11.5), so a DTU is never left in AT mode. Radio parameters
are applied verbatim with no plausibility checking (FR-11.6). Provisioning is
idempotent (NFR-11.1): a second run with the same parameters writes nothing.

Entering AT mode requires the escape terminated with CRLF — `+++\\r\\n`; a bare
`+++` produces no response (§11.1). The port is any serial-like object exposing
`write(bytes)`, `read_all() -> bytes`, and `reset_input_buffer()`.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, Protocol

# AT+KEY is write-only: it cannot be read back and so is never verified (§11.3).
WRITE_ONLY: frozenset[str] = frozenset({"KEY"})

_ENTER_ATTEMPTS = 3
_EXIT_ATTEMPTS = 2


class SerialLike(Protocol):
    """The subset of a serial port the provisioner uses."""

    def write(self, data: bytes) -> Any: ...

    def read_all(self) -> bytes: ...

    def reset_input_buffer(self) -> Any: ...


@dataclass(frozen=True)
class ProvisionResult:
    """Outcome of a provisioning pass."""

    entered: bool
    written: dict[str, str] = field(default_factory=dict)
    mismatches: dict[str, str] = field(default_factory=dict)
    unverifiable: tuple[str, ...] = ()


def _log(logger: Any, event: str, **fields: Any) -> None:
    if logger is not None:
        logger.event(event, **fields)


def _send(port: SerialLike, text: str) -> bytes:
    """Send one CRLF-terminated command (FR-11.4) and return the raw reply."""
    port.reset_input_buffer()
    port.write(text.encode("ascii") + b"\r\n")
    return port.read_all()


def _query(port: SerialLike, name: str) -> str:
    """Read one parameter with `AT+X?` and return its parsed value (FR-11.1)."""
    return _parse_value(_send(port, f"AT+{name}?"))


def _parse_value(raw: bytes) -> str:
    """Extract the value from a DTU reply, tolerating the common reply shapes.

    ``7\\r\\nOK`` → ``7``; ``+SF:7`` → ``7``; ``AT+SF=7`` → ``7``.
    """
    text = raw.decode("ascii", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line or line in ("OK", "ERROR"):
            continue
        if "=" in line:
            return line.rsplit("=", 1)[1].strip()
        if ":" in line:
            return line.split(":", 1)[1].strip()
        return line
    return ""


def _enter_at(port: SerialLike) -> bool:
    # A bare +++ gets no response (§11.1); a non-empty reply means AT mode is up.
    return any(_send(port, "+++").strip() for _ in range(_ENTER_ATTEMPTS))


def _exit_at(port: SerialLike) -> None:
    for _ in range(_EXIT_ATTEMPTS):
        if _send(port, "AT+EXIT").strip():
            return
    # Exit did not acknowledge; reboot is present in firmware, absent from the
    # vendor docs (§11.4). Best effort — the finally path must not raise.
    with contextlib.suppress(OSError):
        _send(port, "AT+REBOOT")


def provision(
    port: SerialLike, params: dict[str, str], *, logger: Any | None = None
) -> ProvisionResult:
    """Configure the locally attached DTU from `params` (`{"SF": "9", ...}`).

    Guarantees `AT+EXIT` on every exit path via try/finally (FR-11.5).
    """
    if not _enter_at(port):
        # AT mode not entered: DTU left untouched, assumed already configured (§11.4).
        _log(logger, "dtu_at_mode_failed")
        return ProvisionResult(entered=False)

    written: dict[str, str] = {}
    mismatches: dict[str, str] = {}
    unverifiable: list[str] = []
    try:
        for name, desired in params.items():
            desired = str(desired)
            current = _query(port, name)
            if current == desired:
                continue
            _send(port, f"AT+{name}={desired}")
            written[name] = desired
            if name in WRITE_ONLY:
                unverifiable.append(name)
                _log(logger, "dtu_written_unverifiable", param=name)
                continue
            actual = _verify(port, name, desired)
            if actual != desired:
                mismatches[name] = actual
                _log(logger, "dtu_readback_mismatch", param=name,
                     wanted=desired, got=actual)
    finally:
        _exit_at(port)

    return ProvisionResult(
        entered=True,
        written=written,
        mismatches=mismatches,
        unverifiable=tuple(unverifiable),
    )


def _verify(port: SerialLike, name: str, desired: str) -> str:
    """Read a written parameter back, retrying once on mismatch (§11.4)."""
    actual = _query(port, name)
    if actual != desired:
        actual = _query(port, name)
    return actual
