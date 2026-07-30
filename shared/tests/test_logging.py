import json
from pathlib import Path

from hornethunter_shared.logging import REDACTED, StructuredLogger


def read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_writes_one_json_object_per_event(tmp_path: Path) -> None:
    log = StructuredLogger(tmp_path / "hh.jsonl")
    log.event("frame_tx", seq=3, carrier="lora")
    log.event("health", station="st1", state="GREEN")
    records = read_lines(tmp_path / "hh.jsonl")
    assert [r["event"] for r in records] == ["frame_tx", "health"]
    assert records[0]["seq"] == 3


def test_every_record_has_wall_and_monotonic_time(tmp_path: Path) -> None:
    log = StructuredLogger(tmp_path / "hh.jsonl")
    log.event("tick")
    record = read_lines(tmp_path / "hh.jsonl")[0]
    assert "wall" in record and "mono" in record


def test_secrets_are_redacted(tmp_path: Path) -> None:
    log = StructuredLogger(tmp_path / "hh.jsonl")
    log.event("settings_push", krakenpro_key="super-secret", center_freq=148.5)
    record = read_lines(tmp_path / "hh.jsonl")[0]
    assert record["krakenpro_key"] == REDACTED
    assert record["center_freq"] == 148.5


def test_rotation_bounds_growth(tmp_path: Path) -> None:
    log = StructuredLogger(tmp_path / "hh.jsonl", max_bytes=200, backup_count=2)
    for i in range(200):
        log.event("spam", i=i, filler="x" * 50)
    assert (tmp_path / "hh.jsonl").stat().st_size <= 400
    assert (tmp_path / "hh.1").exists()
