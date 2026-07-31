"""Kraken settings tests (FSD §14): merge + read-back CRC, route probing, file write."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hornethunter_kraken.settings_client import STATUS_READ_ONLY, KrakenSettings
from hornethunter_shared.registry import canonical_crc


def test_file_route_writes_atomically_with_ext_upd_flag(
    tmp_path: Path, full_settings: dict[str, Any], transport_factory: Any
) -> None:
    share = tmp_path / "_share"
    share.mkdir()
    path = share / "settings.json"
    path.write_text(json.dumps(full_settings))
    settings = KrakenSettings(transport_factory(full_settings), settings_path=str(path))
    assert not settings.read_only  # the file route is available and preferred (§14)

    result = settings.apply_delta({"uniform_gain": 0.5})
    assert result.applied and not result.read_only
    written = json.loads(path.read_text())
    assert written["uniform_gain"] == 0.5
    assert written["ext_upd_flag"] is True  # the DSP watcher will apply it
    assert result.config_crc == settings.config_crc


def test_file_route_falls_back_to_http_when_dir_missing(
    tmp_path: Path, full_settings: dict[str, Any], transport_factory: Any
) -> None:
    missing = tmp_path / "nope" / "settings.json"  # directory does not exist
    settings = KrakenSettings(
        transport_factory(full_settings, json_route=True), settings_path=str(missing)
    )
    assert not settings.read_only  # file route rejected -> HTTP json route used
    assert settings.apply_delta({"uniform_gain": 0.5}).applied


def test_apply_delta_merges_and_recomputes_crc_from_readback(
    full_settings: dict[str, Any], transport_factory: Any
) -> None:
    transport = transport_factory(full_settings, json_route=True)
    settings = KrakenSettings(transport)
    assert settings.read_only is False

    result = settings.apply_delta({"uniform_gain": 30})
    assert result.applied is True
    assert result.read_only is False
    assert not result.altered  # the fake accepts the value unchanged

    expected = canonical_crc({**full_settings, "uniform_gain": 30})
    assert result.config_crc == expected
    assert settings.config_crc == expected
    assert transport.settings["uniform_gain"] == 30


def test_apply_delta_reports_fields_the_kraken_clamped(
    full_settings: dict[str, Any], transport_factory: Any
) -> None:
    def clamp(settings: dict[str, Any]) -> dict[str, Any]:
        settings["uniform_gain"] = 25  # the DAQ refuses anything higher
        return settings

    transport = transport_factory(full_settings, json_route=True, mutate=clamp)
    settings = KrakenSettings(transport)

    result = settings.apply_delta({"uniform_gain": 30})
    assert result.applied is True
    assert result.altered == {"uniform_gain": 25}  # FR-13.3
    assert result.config_crc == canonical_crc({**full_settings, "uniform_gain": 25})


def test_multipart_route_is_discovered_when_json_absent(
    full_settings: dict[str, Any], transport_factory: Any
) -> None:
    transport = transport_factory(full_settings, json_route=False, multipart_route=True)
    settings = KrakenSettings(transport)
    assert settings.read_only is False
    assert settings.apply_delta({"uniform_gain": 22}).applied is True


def test_read_only_when_no_write_route(
    full_settings: dict[str, Any], transport_factory: Any
) -> None:
    transport = transport_factory(full_settings, json_route=False, multipart_route=False)
    settings = KrakenSettings(transport)
    assert settings.read_only is True

    # Reads are still served for divergence detection (FR-13.6).
    assert settings.read()["station_id"] == "kraken-07"
    assert settings.config_crc == canonical_crc(full_settings)

    # A push is rejected with a distinct reason, not a crash.
    result = settings.apply_delta({"uniform_gain": 30})
    assert result.applied is False
    assert result.read_only is True
    assert result.reason == "read_only"
    assert result.status == STATUS_READ_ONLY
