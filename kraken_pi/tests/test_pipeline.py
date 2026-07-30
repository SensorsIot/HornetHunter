"""Bearing pipeline tests (FSD §9): age, no_data, clamp, position-on-change."""

from __future__ import annotations

from hornethunter_kraken.measurement import Measurement
from hornethunter_kraken.pipeline import BearingPipeline
from hornethunter_shared.bearing import AGE_MAX
from hornethunter_shared.geo import LatLon
from hornethunter_shared.messages import (
    FLAG_KRAKEN_LINK_UP,
    FLAG_NO_DATA,
    FLAG_POSITION_PRESENT,
)

REF = LatLon(47.0, 8.0)


def _measurement(**overrides: object) -> Measurement:
    base: dict[str, object] = {
        "bearing_deg": 90.0,
        "confidence": 120.0,
        "power_dbm": -45.0,
        "freq_hz": 148_524_000.0,
        "latitude": None,
        "longitude": None,
        "adc_overdrive": False,
        "squelch_open": False,
        "num_corr_sources": 4,
        "snr": 10.0,
        "mono_ts": 0.0,
    }
    base.update(overrides)
    return Measurement(**base)  # type: ignore[arg-type]


def _pipeline(**kwargs: object) -> BearingPipeline:
    params: dict[str, object] = {
        "station_id": "kraken-07",
        "reference": REF,
        "position_epsilon_dm": 50,
        "max_age_ms": 5000,
        "clock": lambda: 0.0,
    }
    params.update(kwargs)
    return BearingPipeline(**params)  # type: ignore[arg-type]


def test_no_measurement_reports_no_data() -> None:
    report = _pipeline().build(config_version=1, config_crc=2)
    assert not report.has_data
    assert report.flags & FLAG_NO_DATA


def test_age_is_computed_from_injected_clock() -> None:
    pipe = _pipeline()
    pipe.update(_measurement(mono_ts=10.0))
    report = pipe.build(config_version=0, config_crc=0, now=10.4)
    assert report.age_ms == 400  # (10.4 - 10.0) * 1000
    assert report.flags & FLAG_KRAKEN_LINK_UP


def test_age_beyond_max_age_reports_no_data() -> None:
    pipe = _pipeline(max_age_ms=5000)
    pipe.update(_measurement(mono_ts=0.0))
    report = pipe.build(config_version=0, config_crc=0, now=6.0)  # 6000 ms > 5000
    assert report.flags & FLAG_NO_DATA
    assert not report.flags & FLAG_KRAKEN_LINK_UP


def test_age_overflow_is_clamped_and_flagged() -> None:
    pipe = _pipeline(max_age_ms=1_000_000)
    pipe.update(_measurement(mono_ts=0.0))
    report = pipe.build(config_version=0, config_crc=0, now=120.0)  # 120 s > u16 ms
    assert report.age_ms == AGE_MAX
    assert report.flags & FLAG_NO_DATA


def test_out_of_range_bearing_is_discarded_and_previous_retained() -> None:
    pipe = _pipeline()
    assert pipe.update(_measurement(bearing_deg=90.0, mono_ts=0.0)) is True
    assert pipe.update(_measurement(bearing_deg=360.0, mono_ts=0.0)) is False
    assert pipe.update(_measurement(bearing_deg=-1.0, mono_ts=0.0)) is False
    assert pipe.discarded == 2
    assert pipe.produced == 1
    report = pipe.build(config_version=0, config_crc=0, now=0.1)
    assert report.bearing_deg == 90.0  # previous measurement retained


def test_position_transmitted_only_on_change() -> None:
    pipe = _pipeline(position_epsilon_dm=50)  # 5 m
    pipe.update(_measurement(latitude=47.0, longitude=8.0, mono_ts=0.0))
    first = pipe.build(config_version=0, config_crc=0, now=0.1)
    assert first.flags & FLAG_POSITION_PRESENT  # first position always sent

    # A sub-metre jitter stays under the epsilon → no position.
    pipe.update(_measurement(latitude=47.000002, longitude=8.0, mono_ts=0.1))
    second = pipe.build(config_version=0, config_crc=0, now=0.2)
    assert not second.flags & FLAG_POSITION_PRESENT

    # A ~50 m move exceeds the epsilon → position resumes.
    pipe.update(_measurement(latitude=47.00045, longitude=8.0, mono_ts=0.2))
    third = pipe.build(config_version=0, config_crc=0, now=0.3)
    assert third.flags & FLAG_POSITION_PRESENT


def test_reset_counts_clears_since_poll_counters() -> None:
    pipe = _pipeline()
    pipe.update(_measurement(mono_ts=0.0))
    pipe.update(_measurement(bearing_deg=400.0, mono_ts=0.0))
    assert (pipe.produced, pipe.discarded) == (1, 1)
    pipe.reset_counts()
    assert (pipe.produced, pipe.discarded) == (0, 0)
