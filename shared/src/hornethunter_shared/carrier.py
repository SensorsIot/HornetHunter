"""Byte-carrier abstraction (FSD §15.2).

Nothing above the frame layer knows which carrier is in use: a carrier is a
best-effort, non-blocking byte pipe. `recv()` returns whatever bytes are available
right now (possibly a partial or coalesced frame — that is `FrameReader`'s problem),
or empty bytes when there is nothing.

`InProcessLink` is the host-tier simulator: two cross-wired endpoints with optional
whole-frame loss, modelling the LoRa carrier's packet-granular loss (§15.3). The
serial and TCP carriers are the real bench/field carriers and are exercised there,
not at the host tier.
"""

from __future__ import annotations

import random
import socket
from typing import Protocol, runtime_checkable


@runtime_checkable
class Carrier(Protocol):
    """A non-blocking, best-effort byte pipe."""

    def send(self, data: bytes) -> None: ...

    def recv(self) -> bytes: ...

    def close(self) -> None: ...


class InProcessLink:
    """Bidirectional in-process carrier pair for host-tier tests.

    Each `send()` is treated as one packet and dropped whole with probability
    `drop_prob`, matching the DTU's packet-granular loss. Loss is deterministic for
    a given `seed`, so tests are reproducible.
    """

    def __init__(self, drop_prob: float = 0.0, seed: int = 0) -> None:
        self._rng = random.Random(seed)
        self._drop = drop_prob
        self._inbox_a = bytearray()
        self._inbox_b = bytearray()
        self.a: Carrier = _Endpoint(self, "a")
        self.b: Carrier = _Endpoint(self, "b")

    def _deliver(self, target: bytearray, data: bytes) -> None:
        if self._rng.random() >= self._drop:
            target += data

    def _send(self, origin: str, data: bytes) -> None:
        self._deliver(self._inbox_b if origin == "a" else self._inbox_a, data)

    def _recv(self, origin: str) -> bytes:
        inbox = self._inbox_a if origin == "a" else self._inbox_b
        out = bytes(inbox)
        inbox.clear()
        return out


class _Endpoint:
    def __init__(self, link: InProcessLink, side: str) -> None:
        self._link = link
        self._side = side

    def send(self, data: bytes) -> None:
        self._link._send(self._side, data)

    def recv(self) -> bytes:
        return self._link._recv(self._side)

    def close(self) -> None:  # nothing to release in-process
        return None


class SerialCarrier:
    """LoRa DTU over a local serial device or an RFC2217 network port (bench).

    Opened with DTR and RTS deasserted (Appendix A). `pyserial` is imported lazily
    so the host tier never needs it.
    """

    def __init__(self, url: str, baudrate: int = 115200, timeout: float = 0.0) -> None:
        import serial  # type: ignore[import-untyped]  # lazy: bench/field only

        self._port = serial.serial_for_url(url, do_not_open=True)
        self._port.baudrate = baudrate
        self._port.timeout = timeout
        self._port.dtr = False
        self._port.rts = False
        self._port.open()

    def send(self, data: bytes) -> None:
        self._port.write(data)
        self._port.flush()

    def recv(self) -> bytes:
        waiting = self._port.in_waiting
        return bytes(self._port.read(waiting)) if waiting else b""

    def close(self) -> None:
        self._port.close()


class TcpCarrier:
    """WLAN carrier over a connected TCP socket (bench/field)."""

    def __init__(self, sock: socket.socket) -> None:
        sock.setblocking(False)
        self._sock = sock

    def send(self, data: bytes) -> None:
        self._sock.sendall(data)

    def recv(self) -> bytes:
        chunks = []
        try:
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except BlockingIOError:
            pass
        return b"".join(chunks)

    def close(self) -> None:
        self._sock.close()
