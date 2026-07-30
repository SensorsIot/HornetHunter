"""Shared host-tier fixtures: no hardware, no network."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest


def _full_settings() -> dict[str, Any]:
    """A settings dict carrying every crc-covered registry field (§7.4)."""
    return {
        "center_freq": 148.524,
        "uniform_gain": 20,
        "data_interface": "eth0",
        "default_ip": "0.0.0.0",
        "en_doa": True,
        "ant_arrangement": "ULA",
        "ula_direction": "Both",
        "ant_spacing_meters": 0.5,
        "array_offset": 0,
        "doa_method": "MUSIC",
        "doa_decorrelation_method": "FBA",
        "expected_num_of_sources": 1,
        "doa_fig_type": "Compass",
        "en_peak_hold": False,
        "compass_offset": 0.0,
        "spectrum_calculation": "Single",
        "vfo_mode": "Standard",
        "active_vfos": 1,
        "output_vfo": 0,
        "dsp_decimation": 1,
        "en_optimize_short_bursts": False,
        "vfo_freq_0": 148524000.0,
        "vfo_bw_0": 12500.0,
        "vfo_squelch_0": -40,
        "vfo_squelch_mode_0": "Default",
        "vfo_demod_0": "Default",
        "station_id": "kraken-07",
        "location_source": "Static",
        "doa_data_format": "Kraken Pro Local",
        # crc-excluded fields the KrakenSDR owns.
        "latitude": 47.3769,
        "longitude": 8.5417,
        "heading": 0.0,
        "ext_upd_flag": 0,
    }


class FakeTransport:
    """In-memory settings store standing in for the KrakenSDR HTTP endpoints.

    `json_route` / `multipart_route` gate which write route "exists" so the probe
    (FR-13.5) can be exercised. An optional `mutate` clamps written settings so
    read-back divergence (FR-13.3) can be tested.
    """

    def __init__(
        self,
        settings: dict[str, Any],
        *,
        json_route: bool = True,
        multipart_route: bool = False,
        mutate: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.settings = dict(settings)
        self.json_route = json_route
        self.multipart_route = multipart_route
        self._mutate = mutate
        self.writes = 0

    def get(self, url: str) -> bytes:
        return json.dumps(self.settings).encode("utf-8")

    def _store(self, body: bytes) -> int:
        incoming = json.loads(body)
        self.settings = self._mutate(incoming) if self._mutate else incoming
        self.writes += 1
        return 200

    def post_json(self, url: str, body: bytes) -> int:
        return self._store(body) if self.json_route else 404

    def post_multipart(self, url: str, field_name: str, body: bytes) -> int:
        return self._store(body) if self.multipart_route else 404


@pytest.fixture
def full_settings() -> dict[str, Any]:
    return _full_settings()


@pytest.fixture
def transport_factory() -> type[FakeTransport]:
    return FakeTransport
