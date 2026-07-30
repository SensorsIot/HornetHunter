from hornethunter_shared.bearing import decode_bearing, encode_bearing
from hornethunter_shared.geo import LatLon
from hornethunter_shared.messages import (
    FLAG_KRAKEN_LINK_UP,
    FLAG_NO_DATA,
    FLAG_POSITION_PRESENT,
    BearingReport,
)

BASE = BearingReport(
    station_id="kraken-01",
    age_ms=1234,
    bearing_deg=137.42,
    confidence=159.0,  # feed conf is not normalised (§9.3)
    power_dbm=-47.0,
    config_version=5,
    config_crc=0xBEEF,
    flags=FLAG_KRAKEN_LINK_UP,
)


def test_round_trip_without_position_is_10_bytes() -> None:
    wire = encode_bearing(BASE)
    assert len(wire) == 10
    got = decode_bearing(wire, "kraken-01")
    assert got.age_ms == BASE.age_ms
    assert abs(got.bearing_deg - BASE.bearing_deg) < 0.01
    assert got.confidence == 159.0
    assert got.power_dbm == -47.0
    assert got.config_version == 5
    assert got.config_crc == 0xBEEF
    assert not got.has_position


def test_round_trip_with_position_is_14_bytes() -> None:
    ref = LatLon(47.3769, 8.5417)
    report = BearingReport(
        **{**BASE.__dict__, "latitude": 47.3773, "longitude": 8.5425,
           "flags": FLAG_KRAKEN_LINK_UP | FLAG_POSITION_PRESENT}
    )
    wire = encode_bearing(report, ref)
    assert len(wire) == 14
    got = decode_bearing(wire, "kraken-01", ref)
    assert got.has_position
    assert got.latitude is not None and abs(got.latitude - 47.3773) < 1e-4
    assert got.longitude is not None and abs(got.longitude - 8.5425) < 1e-4


def test_position_dropped_when_no_reference_on_encode() -> None:
    report = BearingReport(**{**BASE.__dict__, "latitude": 1.0, "longitude": 2.0})
    # No reference supplied → position omitted, flag clear.
    wire = encode_bearing(report, None)
    assert len(wire) == 10
    assert not (wire[0] & FLAG_POSITION_PRESENT)


def test_no_data_flag_survives_round_trip() -> None:
    report = BearingReport(**{**BASE.__dict__, "flags": FLAG_NO_DATA})
    got = decode_bearing(encode_bearing(report), "kraken-01")
    assert not got.has_data


def test_age_is_clamped_to_u16() -> None:
    report = BearingReport(**{**BASE.__dict__, "age_ms": 100_000})
    got = decode_bearing(encode_bearing(report), "kraken-01")
    assert got.age_ms == 0xFFFF
