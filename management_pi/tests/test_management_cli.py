from pathlib import Path

import pytest

from hornethunter_management import __version__
from hornethunter_management.cli import fix_from_reports, main
from hornethunter_shared.geo import LatLon, distance_m, from_local_enu, initial_bearing_deg
from hornethunter_shared.messages import BearingReport

WEST = LatLon(47.3769, 8.5417)
EAST = from_local_enu(WEST, 1000.0, 0.0)
TARGET = from_local_enu(WEST, 500.0, 500.0)


def report(station: str, at: LatLon, target: LatLon, confidence: float = 0.9) -> BearingReport:
    return BearingReport(
        station_id=station,
        timestamp=1750000000.0,
        latitude=at.lat,
        longitude=at.lon,
        bearing_deg=initial_bearing_deg(at, target),
        confidence=confidence,
        frequency_hz=434_000_000,
    )


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_fix_from_two_stations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "reports.jsonl"
    path.write_text(
        report("kraken-01", WEST, TARGET).to_json()
        + "\n"
        + report("kraken-02", EAST, TARGET).to_json()
        + "\n"
    )

    assert main(["--fix-from", str(path)]) == 0

    lat_text, lon_text = capsys.readouterr().out.strip().split(",")
    fix = LatLon(float(lat_text), float(lon_text))
    assert distance_m(fix, TARGET) < 1.0


def test_no_fix_from_a_single_station(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "reports.jsonl"
    path.write_text(report("kraken-01", WEST, TARGET).to_json() + "\n")

    assert main(["--fix-from", str(path)]) == 1
    assert "no fix" in capsys.readouterr().err


def test_zero_confidence_reports_are_ignored() -> None:
    reports = [
        report("kraken-01", WEST, TARGET, confidence=0.9),
        report("kraken-02", EAST, TARGET, confidence=0.0),
    ]
    assert fix_from_reports(reports) is None


def test_blank_lines_are_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "reports.jsonl"
    path.write_text(
        report("kraken-01", WEST, TARGET).to_json()
        + "\n\n"
        + report("kraken-02", EAST, TARGET).to_json()
        + "\n\n"
    )
    assert main(["--fix-from", str(path)]) == 0
