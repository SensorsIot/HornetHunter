from pathlib import Path

from _settings import full_settings

from hornethunter_management.master import Master, MasterConfig, StationSpec
from hornethunter_management.mirror import ConfigMirror
from hornethunter_management.scheduler import CycleTiming
from hornethunter_shared.bearing import encode_bearing
from hornethunter_shared.carrier import InProcessLink
from hornethunter_shared.frame import Frame, FrameReader, MsgType
from hornethunter_shared.messages import FLAG_KRAKEN_LINK_UP, BearingReport
from hornethunter_shared.registry import decode_delta

ADDR = 1


def build(tmp_path: Path) -> tuple[Master, InProcessLink, FrameReader, ConfigMirror]:
    link = InProcessLink()
    mirror = ConfigMirror(tmp_path / "mirror.json")
    mirror.seed(ADDR, full_settings(uniform_gain=1), version=0)
    config = MasterConfig(
        stations=(StationSpec(addr=ADDR, name="s1", slot_index=0),),
        timing=CycleTiming(period_ms=1000, guard_ms=10, slot_ms=50),
    )
    master = Master(link.a, config, mirror)
    station_reader = FrameReader()  # the fake station's view of the medium
    return master, link, station_reader, mirror


def station_frames(link: InProcessLink, reader: FrameReader) -> list[Frame]:
    return reader.feed(link.b.recv())


def test_poll_is_answered_with_a_stored_bearing(tmp_path: Path) -> None:
    master, link, reader, _ = build(tmp_path)

    master.step(0)  # broadcasts a POLL
    polls = station_frames(link, reader)
    assert any(f.msg_type is MsgType.POLL for f in polls)

    # The fake station answers with a BEARING in its slot.
    report = BearingReport(
        station_id="s1",
        age_ms=500,
        bearing_deg=123.4,
        confidence=50.0,
        power_dbm=-40.0,
        config_version=0,
        config_crc=0,
        flags=FLAG_KRAKEN_LINK_UP,
    )
    answer = Frame(MsgType.BEARING, dest=0xFF, src=ADDR, seq=0, payload=encode_bearing(report))
    link.b.send(answer.encode())

    master.step(20)  # receives and stores it
    stored = master.states[ADDR].last_bearing
    assert stored is not None
    assert abs(stored.bearing_deg - 123.4) < 0.05
    assert stored.power_dbm == -40.0


def test_queued_field_edit_reaches_the_station_as_a_delta(tmp_path: Path) -> None:
    master, link, reader, _ = build(tmp_path)

    master.queue_delta(ADDR, {"uniform_gain": 9})
    master.step(0)

    frames = station_frames(link, reader)
    deltas = [f for f in frames if f.msg_type is MsgType.PARAM_DELTA]
    assert len(deltas) == 1
    assert decode_delta(deltas[0].payload) == {"uniform_gain": 9}


def test_missed_slot_feeds_a_retry_then_health(tmp_path: Path) -> None:
    master, link, reader, _ = build(tmp_path)
    # Never answer the poll; drive the cycle past its deadlines.
    master.step(0)  # begin cycle, POLL
    master.step(100)  # deadline passed -> unicast retry
    master.step(300)  # retry deadline passed -> finalize as missed
    frames = station_frames(link, reader)
    polls = [f for f in frames if f.msg_type is MsgType.POLL]
    # One broadcast POLL plus at least one unicast retry (dest == ADDR).
    assert any(f.dest == ADDR for f in polls)
    assert master.health_snapshot(ADDR).consecutive_misses >= 1
