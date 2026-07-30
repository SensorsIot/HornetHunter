import pytest

from hornethunter_shared.protocol import (
    AckPayload,
    IdentPayload,
    PollPayload,
    Reassembler,
    fragment,
)


def test_poll_round_trip_and_expects() -> None:
    poll = PollPayload(cycle_seq=513, slot_ms=150, expected=0b101)
    got = PollPayload.decode(poll.encode())
    assert got == poll
    assert got.expects(0) and got.expects(2) and not got.expects(1)


def test_ack_round_trip() -> None:
    ack = AckPayload(acked_seq=9, config_version=4, config_crc=0xABCD, status=1)
    assert AckPayload.decode(ack.encode()) == ack


def test_ident_round_trip() -> None:
    ident = IdentPayload(schema_version=2, capabilities=0x00FF)
    assert IdentPayload.decode(ident.encode()) == ident


def test_fragment_reassemble_in_order() -> None:
    data = bytes(range(250)) * 3  # 750 bytes
    frags = fragment(data, max_chunk=200)
    assert len(frags) == 4
    reasm = Reassembler()
    out = None
    for frag in frags:
        out = reasm.add(frag)
    assert out == data


def test_fragment_reassemble_out_of_order() -> None:
    data = b"the quick brown fox" * 20
    frags = fragment(data, max_chunk=32)
    reasm = Reassembler()
    results = [reasm.add(frag) for frag in reversed(frags)]
    assert results[-1] == data
    assert all(r is None for r in results[:-1])


def test_empty_input_is_one_empty_fragment() -> None:
    frags = fragment(b"", max_chunk=64)
    assert len(frags) == 1
    assert Reassembler().add(frags[0]) == b""


def test_bad_max_chunk_rejected() -> None:
    with pytest.raises(ValueError):
        fragment(b"x", max_chunk=0)
