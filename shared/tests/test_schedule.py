"""TDMA schedule tests (FSD §5, §6): pure superframe/slot logic, no clock."""

from __future__ import annotations

from hornethunter_shared.schedule import (
    Scheduler,
    Superframe,
    assign_slots,
    join_offset_ms,
)


def test_superframe_geometry() -> None:
    sf = Superframe(period_ms=1000, slot_ms=125, guard_ms=25)
    assert sf.n_slots == 8
    assert sf.n_data_slots == 6  # 8 - beacon - join
    # data slot 0 is frame slot 2 → [2*125+25, 3*125-25] = [275, 350]
    assert sf.data_slot_window(0) == (275, 350)
    # join window is frame slot 1 → [125+25, 250-25] = [150, 225]
    assert sf.join_window() == (150, 225)


def test_data_slots_are_disjoint_and_guarded() -> None:
    sf = Superframe()
    windows = [sf.data_slot_window(j) for j in range(sf.n_data_slots)]
    for (_s1, e1), (s2, _e2) in zip(windows, windows[1:], strict=False):
        assert e1 <= s2  # guard leaves a gap between consecutive slots


def test_assign_slots_round_robin_compacted() -> None:
    # 3 stations, 6 data slots, cap 2 → each gets 2, in ascending order
    assert assign_slots([3, 1, 2], 6, 2) == [1, 2, 3, 1, 2, 3]


def test_assign_slots_adaptive_rate_when_few_live() -> None:
    # 1 station, 6 slots, cap 2 → it takes 2, the rest idle (no over-transmit)
    assert assign_slots([1], 6, 2) == [1, 1, 0, 0, 0, 0]
    # absent numbers reserve nothing (compaction)
    assert assign_slots([], 6, 2) == [0, 0, 0, 0, 0, 0]


def test_assign_slots_higher_cap_fills_more() -> None:
    assert assign_slots([1], 4, 4) == [1, 1, 1, 1]


def test_scheduler_live_set_staleness() -> None:
    sch = Scheduler(staleness_ms=3000)
    sch.saw(1, now_ms=1000)
    sch.saw(2, now_ms=1500)
    assert sch.live(now_ms=2000) == [1, 2]
    # station 1 goes stale after 3 s without a frame
    assert sch.live(now_ms=4200) == [2]


def test_scheduler_retire_drops_immediately() -> None:
    sch = Scheduler()
    sch.saw(1, 100)
    sch.saw(2, 100)
    sch.retire(1)
    assert sch.live(200) == [2]


def test_scheduler_beacon_advances_seq_and_maps_live() -> None:
    sch = Scheduler(superframe=Superframe(period_ms=1000, slot_ms=125))
    sch.saw(1, 0)
    sch.saw(2, 0)
    seq1, target1, slots1 = sch.beacon(now_ms=10, config_target=0)
    seq2, target2, slots2 = sch.beacon(now_ms=20, config_target=2)
    assert seq1 == 1 and seq2 == 2  # sequence advances each superframe
    assert target2 == 2
    # two live stations, 6 data slots, cap 2 → [1,2,1,2,0,0]
    assert slots1 == (1, 2, 1, 2, 0, 0)


def test_join_offset_within_window() -> None:
    sf = Superframe()
    start, end = sf.join_window()
    for r in (0.0, 0.5, 0.999):
        off = join_offset_ms(sf, r)
        assert start <= off < end
