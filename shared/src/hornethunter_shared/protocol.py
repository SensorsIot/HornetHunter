"""Payload codecs for the fixed-shape HH-Link message types (FSD §10.3).

The variable configuration payloads (`PARAM_DELTA`, `PARAM_FULL`) live in
`registry`, which owns field types. This module covers the fixed messages and the
generic fragmentation used by `PARAM_FULL`/`PARAM_REPORT` (FR-10.6).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

_BEACON = struct.Struct(">HB")  # superframe_seq, config_target
_JOIN = struct.Struct(">B")  # nonce
_ACK = struct.Struct(">BBHB")  # acked_seq, config_version, config_crc, status
_IDENT = struct.Struct(">BH")  # schema_version, capabilities
_FRAG_HEADER = struct.Struct(">BB")  # frag_index, frag_total


@dataclass(frozen=True)
class BeaconPayload:
    """A master beacon (FSD §5.2, §6): its arrival is superframe timing-zero, and
    it carries the ordered data-slot → station map plus the station (if any) with a
    pending config exchange this superframe. No absolute clock is needed — timing is
    relative to when the beacon is heard."""

    seq: int  # superframe sequence (u16, wraps) — detects missed beacons
    config_target: int = 0  # station # with a pending config exchange, or 0 for none
    slots: tuple[int, ...] = ()  # data-slot index → station number (0 = idle)

    def encode(self) -> bytes:
        return _BEACON.pack(self.seq & 0xFFFF, self.config_target & 0xFF) + bytes(
            s & 0xFF for s in self.slots
        )

    @classmethod
    def decode(cls, data: bytes) -> BeaconPayload:
        seq, target = _BEACON.unpack(data[: _BEACON.size])
        return cls(seq, target, tuple(data[_BEACON.size :]))

    def slots_for(self, station: int) -> tuple[int, ...]:
        """The data-slot indices this station owns this superframe (§5.2)."""
        return tuple(i for i, s in enumerate(self.slots) if s == station)


@dataclass(frozen=True)
class JoinPayload:
    """A station's JOIN request (FSD §5.3). The frame's `src` carries the station
    number; the nonce lets the master distinguish a fresh join from a retransmit."""

    nonce: int = 0

    def encode(self) -> bytes:
        return _JOIN.pack(self.nonce & 0xFF)

    @classmethod
    def decode(cls, data: bytes) -> JoinPayload:
        return cls(*_JOIN.unpack(data[: _JOIN.size])) if data else cls()


@dataclass(frozen=True)
class AckPayload:
    """Acknowledges a sequence number and reports config state (FSD §7.5)."""

    acked_seq: int
    config_version: int = 0
    config_crc: int = 0
    status: int = 0  # status flags, e.g. kraken_down

    def encode(self) -> bytes:
        return _ACK.pack(
            self.acked_seq & 0xFF,
            self.config_version & 0xFF,
            self.config_crc & 0xFFFF,
            self.status & 0xFF,
        )

    @classmethod
    def decode(cls, data: bytes) -> AckPayload:
        return cls(*_ACK.unpack(data[: _ACK.size]))


@dataclass(frozen=True)
class IdentPayload:
    """A station's self-identification (FSD §10.3)."""

    schema_version: int
    capabilities: int = 0

    def encode(self) -> bytes:
        return _IDENT.pack(self.schema_version & 0xFF, self.capabilities & 0xFFFF)

    @classmethod
    def decode(cls, data: bytes) -> IdentPayload:
        return cls(*_IDENT.unpack(data[: _IDENT.size]))


def fragment(data: bytes, max_chunk: int) -> list[bytes]:
    """Split `data` into fragment payloads, each prefixed with index and total.

    Each returned payload is `frag_index u8 · frag_total u8 · chunk`. An empty
    input yields a single empty fragment so the set is never zero-length.
    """
    if max_chunk <= 0:
        raise ValueError("max_chunk must be positive")
    chunks = [data[i : i + max_chunk] for i in range(0, len(data), max_chunk)] or [b""]
    if len(chunks) > 0xFF:
        raise ValueError(f"{len(chunks)} fragments exceeds the 255 the header allows")
    total = len(chunks)
    return [_FRAG_HEADER.pack(index, total) + chunk for index, chunk in enumerate(chunks)]


class Reassembler:
    """Collects fragment payloads (FR-10.6) into the original bytes.

    `add()` returns the assembled bytes once every fragment of a set has arrived,
    else None. Fragments may arrive in any order; a fragment whose `frag_total`
    disagrees with the set in progress resets the reassembly.
    """

    def __init__(self) -> None:
        self._total: int | None = None
        self._chunks: dict[int, bytes] = {}

    def add(self, payload: bytes) -> bytes | None:
        if len(payload) < _FRAG_HEADER.size:
            raise ValueError("fragment shorter than its header")
        index, total = _FRAG_HEADER.unpack(payload[: _FRAG_HEADER.size])
        chunk = payload[_FRAG_HEADER.size :]
        if total == 0:
            raise ValueError("frag_total of zero is invalid")
        if self._total != total:
            self._total = total
            self._chunks = {}
        self._chunks[index] = chunk
        if len(self._chunks) != total:
            return None
        assembled = b"".join(self._chunks[i] for i in range(total))
        self.reset()
        return assembled

    def reset(self) -> None:
        self._total = None
        self._chunks = {}
