from hornethunter_management.health import CycleOutcome, HealthEvaluator, HealthState


def delivered(retransmissions: int = 0, exhausted: bool = False) -> CycleOutcome:
    return CycleOutcome(delivered=True, retransmissions=retransmissions, exhausted=exhausted)


def missed() -> CycleOutcome:
    return CycleOutcome(delivered=False, retransmissions=0, exhausted=False)


def fill(ev: HealthEvaluator, outcomes: list[CycleOutcome]) -> None:
    for o in outcomes:
        ev.add_cycle(o)


def test_warming_up_until_window_fills() -> None:
    ev = HealthEvaluator(window_cycles=5)
    fill(ev, [delivered()] * 3)
    snap = ev.snapshot
    assert snap.warming_up
    assert snap.state is HealthState.GREEN


def test_green_when_no_retransmissions() -> None:
    ev = HealthEvaluator(window_cycles=5)
    fill(ev, [delivered()] * 5)
    snap = ev.snapshot
    assert not snap.warming_up
    assert snap.state is HealthState.GREEN
    assert snap.retry_count == 0


def test_orange_when_retries_present_but_all_delivered_under_threshold() -> None:
    ev = HealthEvaluator(window_cycles=10, retry_rate_threshold=0.20)
    fill(ev, [delivered()] * 9 + [delivered(retransmissions=1)])  # rate 0.1
    snap = ev.snapshot
    assert snap.state is HealthState.ORANGE
    assert snap.retry_count == 1
    assert abs(snap.retry_rate - 0.1) < 1e-9


def test_red_when_retry_rate_exceeds_threshold() -> None:
    ev = HealthEvaluator(window_cycles=5, retry_rate_threshold=0.20)
    fill(ev, [delivered()] * 3 + [delivered(retransmissions=1)] * 2)  # rate 0.4
    assert ev.snapshot.state is HealthState.RED


def test_red_when_arq_exhausted_on_any_cycle() -> None:
    ev = HealthEvaluator(window_cycles=5)
    fill(ev, [delivered()] * 4 + [delivered(retransmissions=1, exhausted=True)])
    assert ev.snapshot.state is HealthState.RED


def test_red_on_consecutive_stale_misses() -> None:
    ev = HealthEvaluator(window_cycles=5, stale_cycles=3)
    fill(ev, [delivered()] * 2 + [missed()] * 3)
    snap = ev.snapshot
    assert snap.state is HealthState.RED
    assert snap.consecutive_misses == 3


def test_lost_when_both_carriers_down_and_no_traffic() -> None:
    ev = HealthEvaluator(window_cycles=5, stale_cycles=3)
    ev.carriers_down = True
    fill(ev, [missed()] * 5)
    assert ev.snapshot.state is HealthState.LOST


def test_window_resets_on_carrier_change() -> None:
    ev = HealthEvaluator(window_cycles=5, retry_rate_threshold=0.20)
    fill(ev, [delivered(retransmissions=1)] * 5)  # RED
    assert ev.snapshot.state is HealthState.RED
    ev.reset()  # carrier change (FR-8.5)
    snap = ev.snapshot
    assert snap.warming_up
    assert snap.state is HealthState.GREEN
    assert snap.window_len == 0


def test_rssi_never_contributes() -> None:
    ev = HealthEvaluator(window_cycles=5)
    for _ in range(5):
        ev.add_cycle(CycleOutcome(delivered=True, rssi_dbm=-120.0))  # awful RSSI
    snap = ev.snapshot
    assert snap.state is HealthState.GREEN  # NFR-8.1
    assert snap.last_rssi_dbm == -120.0  # exposed for display only


def test_config_diverged_is_not_a_health_state() -> None:
    assert not hasattr(HealthState, "CONFIG_DIVERGED")
    assert {s.value for s in HealthState} == {"green", "orange", "red", "lost"}
