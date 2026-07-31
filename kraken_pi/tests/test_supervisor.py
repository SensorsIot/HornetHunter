"""Supervisor tests (FSD §5.5, N4/FR-22.2): bounded-escalating autorecovery."""

from __future__ import annotations

from hornethunter_kraken.supervisor import RecoveryPolicy, Supervisor


def _sup() -> tuple[Supervisor, list[int]]:
    calls: list[int] = []
    sup = Supervisor(
        policy=RecoveryPolicy(stall_after_s=10.0, backoff_s=30.0, max_attempts=3),
        recover=lambda: calls.append(1),
    )
    return sup, calls


def test_no_recovery_while_feed_alive() -> None:
    sup, calls = _sup()
    for t in (0.0, 5.0, 9.0, 14.0):
        sup.note_alive(t)  # the feed checks in each loop
        sup.tick(t)
    assert calls == []


def test_recovery_after_stall() -> None:
    sup, calls = _sup()
    sup.note_alive(0.0)
    sup.tick(5.0)  # 5 s stall -> not yet
    assert calls == []
    sup.tick(11.0)  # 11 s stall -> first recovery
    assert calls == [1]


def test_backoff_between_attempts() -> None:
    sup, calls = _sup()
    sup.note_alive(0.0)
    sup.tick(11.0)  # attempt 1
    sup.tick(20.0)  # 9 s since attempt (< 30 backoff) -> none
    assert len(calls) == 1
    sup.tick(45.0)  # 34 s since attempt -> attempt 2
    assert len(calls) == 2


def test_bounded_then_indicated() -> None:
    sup, calls = _sup()
    sup.note_alive(0.0)
    sup.tick(11.0)
    sup.tick(45.0)
    sup.tick(80.0)  # third attempt -> budget spent
    assert len(calls) == 3
    assert sup.indicated
    sup.tick(200.0)  # exhausted -> no further attempts (N4)
    assert len(calls) == 3


def test_note_alive_resets_budget() -> None:
    sup, calls = _sup()
    sup.note_alive(0.0)
    sup.tick(11.0)
    sup.tick(45.0)
    sup.tick(80.0)  # exhausted
    assert sup.indicated and len(calls) == 3
    sup.note_alive(100.0)  # feed recovered -> reset
    assert not sup.indicated
    sup.tick(105.0)  # 5 s since alive -> not stalled
    assert len(calls) == 3
    sup.tick(115.0)  # fresh 15 s stall -> recovery resumes with a fresh budget
    assert len(calls) == 4


def test_recovery_error_does_not_crash() -> None:
    def boom() -> None:
        raise RuntimeError("restart failed")

    sup = Supervisor(
        policy=RecoveryPolicy(stall_after_s=10.0, backoff_s=30.0, max_attempts=1),
        recover=boom,
    )
    sup.note_alive(0.0)
    sup.tick(11.0)  # the action raises; the supervisor swallows and logs it
    assert sup.indicated  # attempt still counted; budget of 1 spent
