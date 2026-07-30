"""Payload codecs for the fixed-shape HH-Link message types (FSD §10.3).

The variable configuration payloads (`PARAM_DELTA`, `PARAM_FULL`) live in
`registry`, which owns field types. This module covers the fixed messages and the
generic fragmentation used by `PARAM_FULL`/`PARAM_REPORT` (FR-10.6).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

_POLL = struct.Struct(">HHH")  # cycle_seq, slot_ms, expected bitmap
_ACK = struct.Struct(">BBHB")  # acked_seq, config_version, config_crc, status
_IDENT = struct.Struct(">BH")  # schema_version, capabilities
_FRAG_HEADER = struct.Struct(">BB")  # frag_index, frag_total


@dataclass(frozen=True)
class PollPayload:
    """A master POLL: which cycle, how wide a reply slot, which stations."""

    cycle_seq: int
    slot_ms: int
    expected: int  # bitmap, bit n set → station n+1 expected

    def encode(self) -> bytes:
        return _POLL.pack(self.cycle_seq & 0xFFFF, self.slot_ms & 0xFFFF, self.expected & 0xFFFF)

    @classmethod
    def decode(cls, data: bytes) -> PollPayload:
        return cls(*_POLL.unpack(data[: _POLL.size]))

    def expects(self, slot_index: int) -> bool:
        return bool(self.expected & (1 << slot_index))


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
