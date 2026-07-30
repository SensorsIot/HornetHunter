"""DoA source tests (FSD §12): synthetic determinism, pure adapter mapping."""

from __future__ import annotations

import itertools

import pytest

from hornethunter_kraken.doa_source import (
    KRAKEN_ADAPTER,
    SIMULATOR_ADAPTER,
    AdapterError,
    SimulatorSource,
    SyntheticSource,
)


def _fixed_clock(start: float = 100.0, step: float = 0.5):
    counter = itertools.count()
    return lambda: start + step * next(counter)


def test_synthetic_source_is_deterministic() -> None:
    a = SyntheticSource(clock=_fixed_clock())
    b = SyntheticSource(clock=_fixed_clock())
    for _ in range(5):
        a.pump()
        b.pump()
    ma, mb = a.latest(), b.latest()
    assert ma is not None and mb is not None
    assert (ma.bearing_deg, ma.confidence, ma.power_dbm) == (
        mb.bearing_deg,
        mb.confidence,
        mb.power_dbm,
    )
    assert a.available is True
    assert a.produced == 5


def test_synthetic_confidence_may_exceed_100() -> None:
    source = SyntheticSource(clock=_fixed_clock())
    seen = []
    for _ in range(6):
        source.pump()
        m = source.latest()
        assert m is not None
        seen.append(m.confidence)
    assert max(seen) > 100.0  # conf is not normalised to 0..1 (§9.3)


def test_kraken_adapter_maps_real_field_names() -> None:
    record = {
        "station_id": "kraken-07",
        "tStamp": 1700000000,
        "radioBearing": 137.5,
        "conf": 159,  # >100 occurs; carried as-is (§9.3)
        "power": -42,
        "freq": 148524000,
        "latitude": 47.3769,
        "longitude": 8.5417,
        "adc_overdrive": 1,
        "num_corr_sources": 4,
        "snr": 12.5,
    }
    m = KRAKEN_ADAPTER.adapt(record, mono_ts=100.0)
    assert m.bearing_deg == 137.5
    assert m.confidence == 159.0
    assert m.power_dbm == -42.0
    assert m.freq_hz == 148524000.0
    assert m.latitude == 47.3769
    assert m.adc_overdrive is True
    assert m.num_corr_sources == 4
    assert m.snr == 12.5
    assert m.mono_ts == 100.0


def test_simulator_adapter_maps_sim_field_names() -> None:
    record = {
        "bearing_deg": 42.0,
        "width_rad": 0.3,
        "rssi_dbfs": -60.0,
        "center_freq_hz": 433920000,
    }
    m = SIMULATOR_ADAPTER.adapt(record, mono_ts=5.0)
    assert m.bearing_deg == 42.0
    assert m.confidence == 0.3  # width_rad → quality
    assert m.power_dbm == -60.0  # rssi_dbfs → power
    assert m.freq_hz == 433920000.0
    assert m.latitude is None  # absent in the simulator record
    assert m.adc_overdrive is False


def test_adapter_rejects_missing_required_field() -> None:
    record = {"conf": 100, "power": -40, "freq": 1.0}  # no radioBearing
    with pytest.raises(AdapterError):
        KRAKEN_ADAPTER.adapt(record, mono_ts=0.0)


def test_adapter_rejects_non_numeric_field() -> None:
    record = {"radioBearing": "north", "conf": 100, "power": -40, "freq": 1.0}
    with pytest.raises(AdapterError):
        KRAKEN_ADAPTER.adapt(record, mono_ts=0.0)


def test_malformed_records_are_discarded_and_counted() -> None:
    good = b'{"bearing_deg": 10, "width_rad": 0.2, "rssi_dbfs": -50, "center_freq_hz": 1}'
    bad = b'{"width_rad": 0.2}'  # missing required fields
    responses = iter([bad, good])

    class _Resp:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    source = SimulatorSource(
        "http://sim/api/v1/doa",
        clock=_fixed_clock(),
        opener=lambda url: _Resp(next(responses)),
    )
    source.pump()  # bad → discarded
    assert source.discarded == 1
    assert source.latest() is None
    source.pump()  # good → produced
    assert source.produced == 1
    assert source.latest() is not None
    assert source.available is True
