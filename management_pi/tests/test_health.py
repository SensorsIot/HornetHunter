from hornethunter_management.health import HealthEvaluator, HealthState


def test_red_before_any_bearing() -> None:
    ev = HealthEvaluator(staleness_threshold_s=1.0)
    assert ev.snapshot(5.0).state is HealthState.RED


def test_green_on_fresh_bearing() -> None:
    ev = HealthEvaluator(staleness_threshold_s=1.0)
    ev.record_bearing(1.0)
    snap = ev.snapshot(1.5)
    assert snap.state is HealthState.GREEN
    assert snap.time_since_last_s is not None
    assert abs(snap.time_since_last_s - 0.5) < 1e-9


def test_red_when_stale_past_threshold() -> None:
    ev = HealthEvaluator(staleness_threshold_s=1.0)
    ev.record_bearing(1.0)
    assert ev.snapshot(2.5).state is HealthState.RED  # 1.5 s > 1.0


def test_returns_green_after_a_new_bearing() -> None:
    ev = HealthEvaluator(staleness_threshold_s=1.0)
    ev.record_bearing(1.0)
    assert ev.snapshot(3.0).state is HealthState.RED
    ev.record_bearing(3.1)
    assert ev.snapshot(3.2).state is HealthState.GREEN


def test_orange_when_rate_low_over_established_window() -> None:
    ev = HealthEvaluator(
        staleness_threshold_s=2.0, rate_window_s=10.0,
        expected_rate_hz=2.3, orange_rate_fraction=0.5,
    )
    t = 0.0
    for _ in range(13):  # ~1 Hz, below 0.5 * 2.3 = 1.15
        ev.record_bearing(t)
        t += 1.0
    snap = ev.snapshot(12.0)  # window established; ~11 arrivals -> ~1.1 Hz
    assert snap.state is HealthState.ORANGE
    assert snap.rate_hz < 1.15


def test_green_when_rate_healthy() -> None:
    ev = HealthEvaluator(
        staleness_threshold_s=1.0, rate_window_s=10.0,
        expected_rate_hz=2.3, orange_rate_fraction=0.5,
    )
    t = 0.0
    for _ in range(120):  # ~10 Hz, well above the fraction
        ev.record_bearing(t)
        t += 0.1
    assert ev.snapshot(t).state is HealthState.GREEN


def test_rssi_is_retained_display_only() -> None:
    ev = HealthEvaluator()
    ev.record_bearing(1.0, rssi_dbm=-120.0)
    assert ev.snapshot(1.1).last_rssi_dbm == -120.0
