import pytest

from hornethunter_shared.messages import (
    FLAG_KRAKEN_LINK_UP,
    FLAG_NO_DATA,
    FLAG_POSITION_PRESENT,
    SCHEMA_VERSION,
    BearingReport,
)

REPORT = BearingReport(
    station_id="kraken-01",
    age_ms=120,
    bearing_deg=45.0,
    confidence=0.82,
    power_dbm=-47.1,
    config_version=3,
    config_crc=0xBEEF,
    flags=FLAG_POSITION_PRESENT | FLAG_KRAKEN_LINK_UP,
    latitude=47.3769,
    longitude=8.5417,
)

# The common case: position rides only on change, so most reports omit it.
REPORT_WITHOUT_POSITION = BearingReport(
    station_id="kraken-01",
    age_ms=120,
    bearing_deg=45.0,
    confidence=0.82,
    power_dbm=-47.1,
    config_version=3,
    config_crc=0xBEEF,
    flags=FLAG_KRAKEN_LINK_UP,
)


def test_json_round_trip() -> None:
    assert BearingReport.from_json(REPORT.to_json()) == REPORT


def test_json_round_trip_without_position() -> None:
    restored = BearingReport.from_json(REPORT_WITHOUT_POSITION.to_json())
    assert restored == REPORT_WITHOUT_POSITION
    assert not restored.has_position


def test_schema_version_is_emitted() -> None:
    assert f'"schema_version":{SCHEMA_VERSION}' in REPORT.to_json()


def test_rejects_other_schema_version() -> None:
    payload = REPORT.to_json().replace(
        f'"schema_version":{SCHEMA_VERSION}', '"schema_version":99'
    )
    with pytest.raises(ValueError, match="unsupported schema_version"):
        BearingReport.from_json(payload)


def test_reports_missing_fields_by_name() -> None:
    with pytest.raises(ValueError, match="bearing_deg"):
        BearingReport.from_json('{"station_id":"kraken-01"}')


def test_position_is_not_a_required_field() -> None:
    payload = REPORT_WITHOUT_POSITION.to_json()
    assert BearingReport.from_json(payload).latitude is None


def test_rejects_non_object_payload() -> None:
    with pytest.raises(ValueError, match="expected a JSON object"):
        BearingReport.from_json("[]")


def test_no_data_flag_marks_a_report_as_carrying_no_measurement() -> None:
    silent = BearingReport(
        station_id="kraken-01",
        age_ms=0xFFFF,
        bearing_deg=0.0,
        confidence=0.0,
        power_dbm=0.0,
        config_version=3,
        config_crc=0xBEEF,
        flags=FLAG_NO_DATA,
    )
    assert not silent.has_data
    assert REPORT.has_data
