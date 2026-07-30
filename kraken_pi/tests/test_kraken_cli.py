import json
from pathlib import Path

import pytest

from hornethunter_kraken import __version__
from hornethunter_kraken.cli import main, synthetic_report
from hornethunter_shared.messages import BearingReport

CONFIG = """
[station]
id = "kraken-07"
latitude = 47.3769
longitude = 8.5417

[radio]
frequency_hz = 148524000

[management]
endpoint = "http://management-pi.local:8000/reports"
"""


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "kraken.toml"
    path.write_text(CONFIG)
    return path


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_self_test_emits_a_valid_report(
    config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(config_file), "--self-test"]) == 0

    payload = capsys.readouterr().out.strip()
    report = BearingReport.from_json(payload)
    assert report.station_id == "kraken-07"
    assert report.has_position
    assert json.loads(payload)["latitude"] == pytest.approx(47.3769)


def test_run_reports_its_endpoint(config_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(config_file)]) == 0
    assert "management-pi.local" in capsys.readouterr().out


def test_missing_config_is_actionable(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="config.example.toml"):
        main(["--config", str(tmp_path / "absent.toml")])


def test_incomplete_config_names_the_missing_key(tmp_path: Path) -> None:
    path = tmp_path / "kraken.toml"
    path.write_text('[station]\nid = "kraken-07"\n')
    with pytest.raises(KeyError, match=r"\[management\].endpoint"):
        main(["--config", str(path)])


def test_synthetic_report_has_zero_confidence() -> None:
    config = {"station": {"id": "kraken-07", "latitude": 1.0, "longitude": 2.0}}
    assert synthetic_report(config).confidence == 0.0
