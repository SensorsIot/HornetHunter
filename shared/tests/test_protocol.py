import pytest

from hornethunter_shared.protocol import (
    AckPayload,
    BeaconPayload,
    IdentPayload,
    JoinPayload,
    Reassembler,
    fragment,
)


def test_beacon_round_trip_and_slots_for() -> None:
    beacon = BeaconPayload(seq=513, config_target=2, slots=(1, 2, 3, 1, 2, 3))
    got = BeaconPayload.decode(beacon.encode())
    assert got == beacon
    assert got.slots_for(1) == (0, 3)  # station 1 owns data slots 0 and 3
    assert got.slots_for(2) == (1, 4)
    assert got.slots_for(9) == ()  # not in the map


def test_beacon_empty_slot_map() -> None:
    beacon = BeaconPayload(seq=1)
    got = BeaconPayload.decode(beacon.encode())
    assert got.seq == 1 and got.config_target == 0 and got.slots == ()


def test_join_round_trip_and_empty() -> None:
    assert JoinPayload.decode(JoinPayload(nonce=7).encode()) == JoinPayload(7)
    assert JoinPayload.decode(b"") == JoinPayload(0)  # empty payload tolerated


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
