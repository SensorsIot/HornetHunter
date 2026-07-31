"""DoA source tests (FSD §12): synthetic determinism, pure adapter mapping."""

from __future__ import annotations

import itertools

import pytest

from hornethunter_kraken.doa_source import (
    SIMULATOR_ADAPTER,
    AdapterError,
    KrakenSource,
    SimulatorSource,
    SyntheticSource,
    VirtualTargetSource,
    build_source,
    parse_doa_csv,
)
from hornethunter_shared.geo import LatLon, initial_bearing_deg

# A single-VFO line in the exact KrakenSDR DOA_value.html field order (§12.3):
# ts_ms, bearing°, conf, power_dBm, freq_Hz, array, latency, station, lat, lon, ...
_CSV_LINE = (
    "1700000000000, 137.5, 159, -42.0, 148524000, UCA, 436, hb9bla-st4, "
    "47.3769, 8.5417, 0, 0, GPS, R, R, R, R, 1.23, 4.56 \n"
)


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


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


def test_virtual_source_points_at_the_target() -> None:
    # Station due south of the target -> bearing ~ north (0 deg), within the jitter.
    station = LatLon(47.3700, 8.5400)
    target = LatLon(47.3800, 8.5400)
    src = VirtualTargetSource(station, target, clock=_fixed_clock(), bearing_noise_deg=0.0)
    src.pump()
    m = src.latest()
    assert m is not None
    assert abs(m.bearing_deg - initial_bearing_deg(station, target)) < 1e-6
    assert m.bearing_deg == pytest.approx(0.0, abs=1.0)
    assert m.latitude == station.lat and m.longitude == station.lon
    assert src.available is True


def test_virtual_source_is_deterministic_for_a_seed() -> None:
    station, target = LatLon(47.37, 8.54), LatLon(47.39, 8.56)
    a = VirtualTargetSource(station, target, clock=_fixed_clock(), seed=7)
    b = VirtualTargetSource(station, target, clock=_fixed_clock(), seed=7)
    for _ in range(5):
        a.pump()
        b.pump()
    ma, mb = a.latest(), b.latest()
    assert ma is not None and mb is not None
    assert ma.bearing_deg == mb.bearing_deg  # same seed -> identical jitter


def test_virtual_source_respects_update_rate() -> None:
    # Clock ticks 0.1 s per pump; at 2.3 Hz (~0.435 s) only every ~5th pump produces.
    station, target = LatLon(47.37, 8.54), LatLon(47.39, 8.56)
    src = VirtualTargetSource(
        station, target, clock=_fixed_clock(start=0.0, step=0.1), update_rate_hz=2.3
    )
    for _ in range(20):
        src.pump()
    assert 3 <= src.produced <= 6  # ~ 20 * 0.1s * 2.3Hz ≈ 4-5 new measurements


def test_distinct_stations_bearings_cross_at_target() -> None:
    target = LatLon(47.3800, 8.5430)
    west = VirtualTargetSource(LatLon(47.3769, 8.5417), target, bearing_noise_deg=0.0)
    east = VirtualTargetSource(LatLon(47.3769, 8.5450), target, bearing_noise_deg=0.0)
    west.pump()
    east.pump()
    mw, me = west.latest(), east.latest()
    assert mw is not None and me is not None
    # The two stations see the target on materially different bearings (they cross).
    assert abs(mw.bearing_deg - me.bearing_deg) > 5.0


def test_build_source_virtual_requires_target() -> None:
    with pytest.raises(ValueError, match="virtual backend needs"):
        build_source("virtual", latitude=47.0, longitude=8.0)  # no target
    src = build_source("virtual", latitude=47.0, longitude=8.0, target_lat=47.1, target_lon=8.1)
    assert isinstance(src, VirtualTargetSource)


def test_synthetic_confidence_may_exceed_100() -> None:
    source = SyntheticSource(clock=_fixed_clock())
    seen = []
    for _ in range(6):
        source.pump()
        m = source.latest()
        assert m is not None
        seen.append(m.confidence)
    assert max(seen) > 100.0  # conf is not normalised to 0..1 (§9.3)


def test_parse_doa_csv_maps_positional_fields() -> None:
    parsed = parse_doa_csv(_CSV_LINE, mono_ts=100.0)
    assert parsed is not None
    timestamp_ms, m = parsed
    assert timestamp_ms == 1700000000000
    assert m.bearing_deg == 137.5
    assert m.confidence == 159.0  # >100 occurs; carried as-is (§9.3)
    assert m.power_dbm == -42.0
    assert m.freq_hz == 148524000.0
    assert m.latitude == 47.3769
    assert m.longitude == 8.5417
    assert m.squelch_open is True  # a written line means the VFO passed squelch
    assert m.mono_ts == 100.0


def test_parse_doa_csv_empty_file_is_no_bearing_not_error() -> None:
    assert parse_doa_csv("", mono_ts=0.0) is None
    assert parse_doa_csv("   \n  \n", mono_ts=0.0) is None


def test_parse_doa_csv_takes_last_line_of_multi_vfo() -> None:
    two = _CSV_LINE + "1700000000000, 42.0, 80, -55, 434000000, UCA, 400, st, 0, 0 \n"
    parsed = parse_doa_csv(two, mono_ts=1.0)
    assert parsed is not None
    _, m = parsed
    assert m.bearing_deg == 42.0  # last VFO line wins


def test_parse_doa_csv_rejects_short_line() -> None:
    with pytest.raises(AdapterError):
        parse_doa_csv("1, 2, 3 \n", mono_ts=0.0)


def test_kraken_source_emits_only_on_new_timestamp() -> None:
    frame2 = _CSV_LINE.replace("1700000000000", "1700000000437").replace("137.5", "200.0")
    responses = iter([_CSV_LINE.encode(), _CSV_LINE.encode(), frame2.encode()])
    source = KrakenSource(
        "http://kraken/DOA_value.html",
        clock=_fixed_clock(),
        opener=lambda url: _Resp(next(responses)),
    )
    source.pump()  # first frame → produced
    assert source.produced == 1
    assert source.latest() is not None and source.latest().bearing_deg == 137.5
    source.pump()  # same timestamp → not a new measurement
    assert source.produced == 1
    source.pump()  # advanced timestamp → produced again
    assert source.produced == 2
    assert source.latest().bearing_deg == 200.0
    assert source.available is True


def test_kraken_source_empty_file_available_but_no_bearing() -> None:
    source = KrakenSource(
        "http://kraken/DOA_value.html",
        clock=_fixed_clock(),
        opener=lambda url: _Resp(b""),
    )
    source.pump()
    assert source.available is True  # feed up (squelch closed → no output)
    assert source.produced == 0
    assert source.latest() is None


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


def test_simulator_adapter_rejects_missing_required_field() -> None:
    record = {"width_rad": 0.3, "rssi_dbfs": -50, "center_freq_hz": 1.0}  # no bearing_deg
    with pytest.raises(AdapterError):
        SIMULATOR_ADAPTER.adapt(record, mono_ts=0.0)


def test_simulator_adapter_rejects_non_numeric_field() -> None:
    record = {"bearing_deg": "north", "width_rad": 0.3, "rssi_dbfs": -50, "center_freq_hz": 1.0}
    with pytest.raises(AdapterError):
        SIMULATOR_ADAPTER.adapt(record, mono_ts=0.0)


def test_malformed_records_are_discarded_and_counted() -> None:
    good = b'{"bearing_deg": 10, "width_rad": 0.2, "rssi_dbfs": -50, "center_freq_hz": 1}'
    bad = b'{"width_rad": 0.2}'  # missing required fields
    responses = iter([bad, good])

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
