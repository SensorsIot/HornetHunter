"""LoRa DTU provisioning over the SX1262 AT command set (FSD §12).

On startup a node reads the DTU's current parameters, writes only the ones that
differ (FR-12.1, FR-12.2), verifies each written parameter by read-back except the
write-only `AT+KEY` (FR-12.3), and **guarantees `AT+EXIT` on every exit path,
including on error** (FR-12.5), so a DTU is never left in AT mode. Radio parameters
are applied verbatim with no plausibility checking (FR-12.6). Provisioning is
idempotent (NFR-12.1): a second run with the same parameters writes nothing.

Both tiers provision a DTU — the station addresses itself by its number, the master
by `0xFFFF` (§19.2) — so this lives in the shared package alongside the carrier.
`provision()` is pure (any `SerialLike` object); `provision_device()` opens a real
serial port, provisions, and always closes it. `params_from_config()` turns a
`[dtu]` config table into the AT parameter set, forcing `MODE=1` (the transparent
Stream pipe the carrier requires) and taking `ADDR` from the node's address.

Entering AT mode requires the escape terminated with CRLF — `+++\\r\\n`; a bare
`+++` produces no response (§12.1). The port is any serial-like object exposing
`write(bytes)`, `read_all() -> bytes`, and `reset_input_buffer()`.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

# AT+KEY is write-only: it cannot be read back and so is never verified (§12.3).
WRITE_ONLY: frozenset[str] = frozenset({"KEY"})

# [dtu] config key -> AT parameter name. `channel` is special-cased to TXCH+RXCH.
_RADIO_KEYS: dict[str, str] = {
    "sf": "SF",
    "bw": "BW",
    "cr": "CR",
    "power": "PWR",
    "netid": "NETID",
    "key": "KEY",
}

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
    """Send one CRLF-terminated command (FR-12.4) and return the raw reply."""
    port.reset_input_buffer()
    port.write(text.encode("ascii") + b"\r\n")
    return port.read_all()


def _query(port: SerialLike, name: str) -> str:
    """Read one parameter with `AT+X?` and return its parsed value (FR-12.1)."""
    return _parse_value(_send(port, f"AT+{name}?"))


def _parse_value(raw: bytes) -> str:
    """Extract the value from a DTU reply, tolerating the common reply shapes.

    The DTU **echoes the command** with echo enabled, so a query reply is typically
    ``AT+SF?\\r\\n+SF:7\\r\\nOK`` — the echoed query line (`AT+SF?`) must be skipped or
    it is mistaken for the value. Handled shapes:
    ``AT+SF?\\r\\n+SF=7\\r\\nOK`` → ``7``; ``+SF:7`` → ``7``; ``AT+SF?\\r\\n7\\r\\nOK`` →
    ``7``; ``7\\r\\nOK`` → ``7``.
    """
    text = raw.decode("ascii", errors="replace")
    fallback = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line in ("OK", "ERROR"):
            continue
        if line.endswith("?"):
            continue  # the echoed query command, e.g. "AT+MODE?"
        if "=" in line:
            return line.rsplit("=", 1)[1].strip()  # "+MODE=1" / "AT+MODE=1" -> "1"
        if ":" in line:
            return line.split(":", 1)[1].strip()  # "+SF:7" -> "7"
        if not fallback and not line.upper().startswith("AT"):
            fallback = line  # a bare value like "7", but never an echoed command
    return fallback


def _enter_at(port: SerialLike) -> bool:
    # A bare +++ gets no response (§12.1); a non-empty reply means AT mode is up.
    return any(_send(port, "+++").strip() for _ in range(_ENTER_ATTEMPTS))


def _exit_at(port: SerialLike) -> None:
    for _ in range(_EXIT_ATTEMPTS):
        if _send(port, "AT+EXIT").strip():
            return
    # Exit did not acknowledge; reboot is present in firmware, absent from the
    # vendor docs (§12.4). Best effort — the finally path must not raise.
    with contextlib.suppress(OSError):
        _send(port, "AT+REBOOT")


def provision(
    port: SerialLike, params: dict[str, str], *, logger: Any | None = None
) -> ProvisionResult:
    """Configure the locally attached DTU from `params` (`{"SF": "9", ...}`).

    Guarantees `AT+EXIT` on every exit path via try/finally (FR-12.5).
    """
    if not _enter_at(port):
        # AT mode not entered: DTU left untouched, assumed already configured (§12.4).
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
    """Read a written parameter back, retrying once on mismatch (§12.4)."""
    actual = _query(port, name)
    if actual != desired:
        actual = _query(port, name)
    return actual


def params_from_config(dtu_cfg: Mapping[str, Any], *, address: int) -> dict[str, str]:
    """Assemble the AT parameter set to provision from a `[dtu]` config table.

    `MODE` is forced to ``"1"`` — the transparent Stream pipe the carrier requires;
    Packet mode (`MODE=2`) prefixes an address header the pipe does not emit (§19.2).
    `ADDR` is the node's HH-Link address: the station number, or ``0xFFFF`` for the
    master. `channel` maps to both TX and RX channels. Radio parameters (`sf`, `bw`,
    `cr`, `power`, `netid`, `key`) are optional and applied verbatim (FR-12.6).
    """
    params: dict[str, str] = {"MODE": "1", "ADDR": str(int(address))}
    channel = dtu_cfg.get("channel")
    if channel is not None:
        params["TXCH"] = str(int(channel))
        params["RXCH"] = str(int(channel))
    for key, at_name in _RADIO_KEYS.items():
        value = dtu_cfg.get(key)
        if value is not None:
            params[at_name] = str(value)
    return params


class _SettleSerial:
    """Adapts a pyserial port to the `SerialLike` the provisioner expects.

    `read_all()` waits one settle period for the DTU to answer, then drains the
    input buffer — the port is opened non-blocking, so an immediate read would race
    the reply. The settle time matches the ~0.6 s the SX1262 needs to acknowledge.
    """

    def __init__(self, port: Any, settle_s: float) -> None:
        self._port = port
        self._settle = settle_s

    def write(self, data: bytes) -> None:
        self._port.write(data)
        self._port.flush()

    def reset_input_buffer(self) -> None:
        self._port.reset_input_buffer()

    def read_all(self) -> bytes:
        time.sleep(self._settle)
        waiting = self._port.in_waiting
        return bytes(self._port.read(waiting)) if waiting else b""


def provision_device(
    serial_url: str,
    params: dict[str, str],
    *,
    settle_s: float = 0.6,
    logger: Any | None = None,
) -> ProvisionResult:  # pragma: no cover - real serial hardware
    """Open the DTU at `serial_url`, provision it from `params`, always close it.

    Opened with DTR and RTS deasserted (Appendix A), matching `SerialCarrier`, so
    the very same device can be reopened as the transparent pipe immediately after.
    `pyserial` is imported lazily so the host tier never needs it.
    """
    import serial  # type: ignore[import-untyped]  # lazy: bench/field only

    port = serial.serial_for_url(serial_url, do_not_open=True)
    port.baudrate = 115200
    port.timeout = 0.5
    port.dtr = False
    port.rts = False
    port.open()
    try:
        return provision(_SettleSerial(port, settle_s), params, logger=logger)
    finally:
        port.close()


def maybe_provision_dtu(
    config: Mapping[str, Any],
    serial_url: str,
    *,
    address: int,
    logger: Any | None = None,
) -> ProvisionResult | None:
    """Provision the DTU from the `[dtu]` config section if it is `enabled`.

    Returns the `ProvisionResult`, or `None` when the section is absent or disabled
    — the safe default that leaves an already-configured stick untouched. Both
    tiers call this at startup, before the carrier opens the port as a byte pipe.
    """
    dtu_cfg = config.get("dtu")
    if not isinstance(dtu_cfg, Mapping) or not dtu_cfg.get("enabled", False):
        return None
    return provision_device(
        serial_url,
        params_from_config(dtu_cfg, address=address),
        settle_s=float(dtu_cfg.get("settle_s", 0.6)),
        logger=logger,
    )
