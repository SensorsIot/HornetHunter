import pytest

from hornethunter_shared.frame import (
    MAX_PAYLOAD,
    Frame,
    FrameError,
    FrameReader,
    MsgType,
    decode,
)

ALL_TYPES = list(MsgType)


def make(msg_type: MsgType = MsgType.BEARING, payload: bytes = b"hello") -> Frame:
    return Frame(msg_type, dest=0x01, src=0xFF, seq=7, payload=payload)


def test_round_trip_every_type() -> None:
    for msg_type in ALL_TYPES:
        frame = make(msg_type, payload=bytes([msg_type]) * 3)
        assert decode(frame.encode()) == frame


def test_reader_round_trip_every_type() -> None:
    reader = FrameReader()
    for msg_type in ALL_TYPES:
        frame = make(msg_type)
        assert reader.feed(frame.encode()) == [frame]


def test_max_payload_ok_and_oversize_rejected() -> None:
    assert decode(make(payload=b"x" * MAX_PAYLOAD).encode()).payload == b"x" * MAX_PAYLOAD
    with pytest.raises(FrameError):
        make(payload=b"x" * (MAX_PAYLOAD + 1))


def test_split_delivery_reassembles() -> None:
    reader = FrameReader()
    wire = make(payload=b"split me").encode()
    got: list[Frame] = []
    for byte in wire:  # one byte at a time
        got += reader.feed(bytes([byte]))
    assert got == [make(payload=b"split me")]


def test_coalesced_frames_all_extracted() -> None:
    reader = FrameReader()
    a, b = make(MsgType.POLL, b"a"), make(MsgType.BEARING, b"bb")
    assert reader.feed(a.encode() + b.encode()) == [a, b]


def test_garbage_prefix_is_discarded_and_counted() -> None:
    reader = FrameReader()
    frame = make(payload=b"clean")
    assert reader.feed(b"\x00\x11garbage\x22" + frame.encode()) == [frame]
    assert reader.resync_discards == len(b"\x00\x11garbage\x22")


def test_rssi_byte_stripped_before_crc() -> None:
    frame = make(payload=b"rssi")
    with_rssi = frame.encode() + b"\x9c"  # carrier-appended RSSI
    assert decode(with_rssi, rssi_appended=True) == frame

    reader = FrameReader(rssi_appended=True)
    assert reader.feed(with_rssi) == [frame]


def test_corrupt_crc_dropped_then_recovers_next_frame() -> None:
    reader = FrameReader()
    good = make(payload=b"good")
    corrupt = bytearray(make(payload=b"bad!").encode())
    corrupt[-1] ^= 0xFF  # break the CRC
    frames = reader.feed(bytes(corrupt) + good.encode())
    assert frames == [good]
    assert reader.crc_failures >= 1


def test_unsupported_version_is_rejected() -> None:
    frame = Frame(MsgType.BEARING, dest=1, src=2, seq=0, payload=b"", version=2)
    with pytest.raises(FrameError, match="version"):
        decode(frame.encode())
