"""ARQ over the in-process lossy link (FSD AT-2, §10.5)."""

from __future__ import annotations

from hornethunter_shared.arq import (
    DedupReceiver,
    StopAndWaitSender,
    TxEvent,
    make_ack,
)
from hornethunter_shared.carrier import InProcessLink
from hornethunter_shared.frame import FrameReader, MsgType
from hornethunter_shared.protocol import AckPayload

MASTER, STATION = 0xFF, 0x01


def _run(drop_prob: float, n_messages: int, *, seed: int, max_attempts: int) -> dict:
    """Deliver `n_messages` PARAM_DELTAs master→station under whole-frame loss.

    Returns applied payloads and how many transactions exhausted. Uses a virtual
    millisecond clock; every send() is one packet, dropped whole with `drop_prob`.
    """
    link = InProcessLink(drop_prob=drop_prob, seed=seed)
    sender = StopAndWaitSender(dest=STATION, src=MASTER, timeout_ms=400, max_attempts=max_attempts)
    master_reader, station_reader = FrameReader(), FrameReader()
    dedup = DedupReceiver()
    applied: list[bytes] = []
    exhausted = 0
    now = 0

    for i in range(n_messages):
        frame = sender.start(MsgType.PARAM_DELTA, bytes([i]), now)
        link.a.send(frame.encode())

        for _ in range(100_000):
            # Station side: apply new deltas, always ACK.
            for f in station_reader.feed(link.b.recv()):
                if f.msg_type == MsgType.PARAM_DELTA:
                    if dedup.accept(f):
                        applied.append(f.payload)
                    link.b.send(make_ack(dest=f.src, src=f.dest, acked_seq=f.seq).encode())
            # Master side: consume ACKs.
            for f in master_reader.feed(link.a.recv()):
                if f.msg_type == MsgType.ACK:
                    sender.on_ack(AckPayload.decode(f.payload).acked_seq)
            if not sender.busy:
                break
            now = sender.deadline_ms
            event, retx = sender.on_timeout(now)
            if event is TxEvent.RETRANSMIT and retx is not None:
                link.a.send(retx.encode())
            else:
                exhausted += 1
                break
        else:  # pragma: no cover - safety net
            raise AssertionError("transaction did not converge")

    return {"applied": applied, "exhausted": exhausted}


def test_no_loss_delivers_all_exactly_once() -> None:
    result = _run(0.0, 20, seed=1, max_attempts=3)
    assert result["exhausted"] == 0
    assert result["applied"] == [bytes([i]) for i in range(20)]


def test_ten_percent_loss_delivers_all() -> None:
    result = _run(0.10, 20, seed=7, max_attempts=60)
    assert result["exhausted"] == 0
    assert result["applied"] == [bytes([i]) for i in range(20)]


def test_fifty_percent_loss_delivers_all() -> None:
    result = _run(0.50, 20, seed=1234, max_attempts=60)
    assert result["exhausted"] == 0
    assert result["applied"] == [bytes([i]) for i in range(20)]


def test_total_loss_reports_exhaustion_and_applies_nothing() -> None:
    result = _run(1.0, 3, seed=1, max_attempts=3)
    assert result["exhausted"] == 3
    assert result["applied"] == []


def test_duplicate_delta_applied_once() -> None:
    # A retransmission (same seq) is re-ACKed but applied only once (FR-10.7).
    dedup = DedupReceiver()
    sender = StopAndWaitSender(dest=STATION, src=MASTER)
    frame = sender.start(MsgType.PARAM_DELTA, b"\x01", now_ms=0)
    assert dedup.accept(frame) is True
    assert dedup.accept(frame) is False  # retransmit of the same seq
