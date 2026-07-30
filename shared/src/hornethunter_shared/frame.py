"""HH-Link frame codec (FSD §10.2, §10.3).

One frame format runs over both carriers. The LoRa carrier is a transparent byte
pipe with no frame boundaries — one write may arrive split or coalesced — so frames
are self-delimiting by sync word, length and CRC, and `FrameReader` recovers from
arbitrary garbage without operator action (FR-10.1).

    ┌────────┬─────────┬──────┬─────┬─────┬─────┬───────────┬───────┐
    │ SYNC   │ VER/TYPE│ DEST │ SRC │ SEQ │ LEN │  PAYLOAD  │ CRC16 │
    │ 2 B    │  1 B    │ 1 B  │ 1 B │ 1 B │ 1 B │ 0..200 B  │  2 B  │
    └────────┴─────────┴──────┴─────┴─────┴─────┴───────────┴───────┘

The CRC (big-endian) covers VER/TYPE..PAYLOAD. The codec is a pure function of
bytes, independent of carrier and wall-clock time (NFR-10.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from .crc import crc16_ccitt_false

SYNC = b"\xa5\x5a"
PROTOCOL_VERSION = 1
MAX_PAYLOAD = 200
_HEADER_LEN = len(SYNC) + 5  # sync + ver/type + dest + src + seq + len
_OVERHEAD = _HEADER_LEN + 2  # + CRC16


class MsgType(IntEnum):
    """HH-Link message types (FSD §10.3). Values occupy the low nibble of VER/TYPE."""

    POLL = 0x1
    BEARING = 0x2
    ACK = 0x3
    PARAM_DELTA = 0x4
    PARAM_FULL = 0x5
    PARAM_REQ = 0x6
    PARAM_REPORT = 0x7
    IDENT = 0x8


class FrameError(ValueError):
    """A byte sequence is not a valid, current-version frame."""


@dataclass(frozen=True)
class Frame:
    """One HH-Link frame. Addresses and seq are bytes; payload is opaque here."""

    msg_type: MsgType
    dest: int
    src: int
    seq: int
    payload: bytes = b""
    version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if not 0 <= self.version <= 0xF:
            raise FrameError(f"version {self.version} does not fit 4 bits")
        if len(self.payload) > MAX_PAYLOAD:
            raise FrameError(
                f"payload {len(self.payload)} B exceeds {MAX_PAYLOAD} B (FR-10.3)"
            )
        for name in ("dest", "src", "seq"):
            value = getattr(self, name)
            if not 0 <= value <= 0xFF:
                raise FrameError(f"{name} {value} does not fit one byte")

    def encode(self) -> bytes:
        """Serialise to wire bytes, sync word and CRC included."""
        body = bytes(
            (
                (self.version << 4) | (self.msg_type & 0x0F),
                self.dest,
                self.src,
                self.seq,
                len(self.payload),
            )
        ) + self.payload
        crc = crc16_ccitt_false(body)
        return SYNC + body + crc.to_bytes(2, "big")


def decode(data: bytes, *, rssi_appended: bool = False) -> Frame:
    """Decode exactly one framed message. Raises FrameError on any defect.

    When `rssi_appended` the carrier has added one RSSI byte after the CRC (§11.3);
    it is stripped before CRC validation (FR-10.4).
    """
    if rssi_appended:
        if not data:
            raise FrameError("empty input with rssi_appended")
        data = data[:-1]
    if len(data) < _OVERHEAD:
        raise FrameError(f"too short: {len(data)} B < {_OVERHEAD} B minimum")
    if data[: len(SYNC)] != SYNC:
        raise FrameError("bad sync word")
    payload_len = data[6]
    expected = _OVERHEAD + payload_len
    if len(data) != expected:
        raise FrameError(f"length {len(data)} B != declared {expected} B")
    body = data[len(SYNC) : _HEADER_LEN + payload_len]
    received_crc = int.from_bytes(data[_HEADER_LEN + payload_len :], "big")
    if crc16_ccitt_false(body) != received_crc:
        raise FrameError("CRC mismatch")
    ver_type = body[0]
    version = ver_type >> 4
    if version != PROTOCOL_VERSION:
        raise FrameError(f"unsupported version {version}")
    return Frame(
        msg_type=MsgType(ver_type & 0x0F),
        dest=body[1],
        src=body[2],
        seq=body[3],
        payload=bytes(body[5 : 5 + payload_len]),
        version=version,
    )


@dataclass
class FrameReader:
    """Resynchronising stream parser for a transparent byte carrier.

    `feed()` accepts whatever a read returned — a partial frame, one frame, several
    coalesced frames, or garbage — and returns every complete, CRC-valid frame in
    order (FR-10.1, §10.6). Rejected bytes are counted, never partially interpreted.
    """

    rssi_appended: bool = False
    resync_discards: int = 0
    crc_failures: int = 0
    version_mismatches: int = 0
    _buf: bytearray = field(default_factory=bytearray)

    def feed(self, data: bytes) -> list[Frame]:
        self._buf += data
        frames: list[Frame] = []
        while (frame := self._next_frame()) is not None:
            frames.append(frame)
        return frames

    def _next_frame(self) -> Frame | None:
        """Return the next complete, valid frame, or None if more bytes are needed.

        Garbage and CRC-failed candidates are discarded in place (counted), so the
        loop only ever exits with a frame or a genuine need for more bytes.
        """
        while True:
            start = self._buf.find(SYNC)
            if start == -1:
                # No sync in view. Keep only a possible split sync-word prefix.
                keep = len(SYNC) - 1
                if len(self._buf) > keep:
                    self.resync_discards += len(self._buf) - keep
                    if keep:
                        del self._buf[:-keep]
                    else:
                        self._buf.clear()
                return None
            if start > 0:
                self.resync_discards += start
                del self._buf[:start]
                continue

            if len(self._buf) < _HEADER_LEN:
                return None  # header incomplete
            payload_len = self._buf[6]
            total = _OVERHEAD + payload_len + (1 if self.rssi_appended else 0)
            if len(self._buf) < total:
                return None  # frame incomplete

            candidate = bytes(self._buf[:total])
            try:
                frame = decode(candidate, rssi_appended=self.rssi_appended)
            except FrameError as exc:
                self._count_failure(exc)
                # False or corrupt sync: drop one byte and resynchronise past it.
                self.resync_discards += 1
                del self._buf[:1]
                continue
            del self._buf[:total]
            return frame

    def _count_failure(self, exc: FrameError) -> None:
        message = str(exc)
        if "version" in message:
            self.version_mismatches += 1
        else:
            self.crc_failures += 1
