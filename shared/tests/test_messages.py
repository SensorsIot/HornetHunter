import pytest

from hornethunter_shared.messages import SCHEMA_VERSION, BearingReport

REPORT = BearingReport(
    station_id="kraken-01",
    timestamp=1750000000.0,
    latitude=47.3769,
    longitude=8.5417,
    bearing_deg=45.0,
    confidence=0.82,
    frequency_hz=434_000_000,
)


def test_json_round_trip() -> None:
    assert BearingReport.from_json(REPORT.to_json()) == REPORT


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


def test_rejects_non_object_payload() -> None:
    with pytest.raises(ValueError, match="expected a JSON object"):
        BearingReport.from_json("[]")
