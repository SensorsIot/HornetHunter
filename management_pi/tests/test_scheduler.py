from hornethunter_management.scheduler import BROADCAST_ADDR, CycleTiming, PollScheduler
from hornethunter_shared.frame import MsgType, decode
from hornethunter_shared.protocol import PollPayload

TIMING = CycleTiming(period_ms=1000, guard_ms=40, slot_ms=150)


def make() -> PollScheduler:
    return PollScheduler([1, 2], TIMING)


def test_slot_assignment_is_by_position() -> None:
    sched = PollScheduler([5, 9, 3], TIMING)
    assert sched.slot_index(5) == 0
    assert sched.slot_index(9) == 1
    assert sched.slot_index(3) == 2


def test_start_cycle_broadcasts_expected_bitmap() -> None:
    sched = make()
    frame = sched.start_cycle(0)
    assert frame.msg_type is MsgType.POLL
    assert frame.dest == BROADCAST_ADDR
    payload = PollPayload.decode(frame.payload)
    assert payload.slot_ms == 150
    assert payload.expects(0) and payload.expects(1)
    assert payload.cycle_seq == 1


def test_cycle_seq_increments_each_cycle() -> None:
    sched = make()
    assert PollPayload.decode(sched.start_cycle(0).payload).cycle_seq == 1
    assert PollPayload.decode(sched.start_cycle(1000).payload).cycle_seq == 2


def test_on_time_and_late_bearings() -> None:
    sched = make()
    sched.start_cycle(0)
    # station 1 slot deadline = 0 + 40 + 1*150 = 190
    assert sched.record_bearing(1, now_ms=100).on_time
    assert sched.record_bearing(2, now_ms=500).late  # slot 2 deadline = 340


def test_unconfigured_source_is_rejected() -> None:
    sched = make()
    sched.start_cycle(0)
    result = sched.record_bearing(99, now_ms=10)
    assert not result.known


def test_missed_slot_and_unicast_retry() -> None:
    sched = make()
    sched.start_cycle(0)
    sched.record_bearing(1, now_ms=100)  # station 1 answers, station 2 silent
    missed = sched.missed_slots(now_ms=500)
    assert missed == [2]
    retry = sched.retry_poll(2, now_ms=500)
    assert retry.msg_type is MsgType.POLL
    assert retry.dest == 2  # unicast, not broadcast (FR-5.5)
    payload = PollPayload.decode(retry.payload)
    assert payload.expects(1) and not payload.expects(0)


def test_injected_clock_records_jitter() -> None:
    sched = make()
    sched.start_cycle(0)  # next expected at 1000
    sched.start_cycle(1025)  # 25 ms late
    assert sched.jitter_ms == 25


def test_frame_round_trips_on_the_wire() -> None:
    sched = make()
    frame = sched.start_cycle(0)
    assert decode(frame.encode()).msg_type is MsgType.POLL
