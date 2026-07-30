from pathlib import Path

from _settings import full_settings

from hornethunter_management.master import Master, MasterConfig, StationSpec
from hornethunter_management.mirror import ConfigMirror
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
        stations=(StationSpec(addr=ADDR, name="s1"),),
        staleness_threshold_s=1.0,
    )
    master = Master(link.a, config, mirror)
    station_reader = FrameReader()  # the fake station's view of the medium
    return master, link, station_reader, mirror


def station_frames(link: InProcessLink, reader: FrameReader) -> list[Frame]:
    return reader.feed(link.b.recv())


def _bearing(deg: float = 123.4) -> Frame:
    report = BearingReport(
        station_id="s1",
        age_ms=500,
        bearing_deg=deg,
        confidence=50.0,
        power_dbm=-40.0,
        config_version=0,
        config_crc=0,
        flags=FLAG_KRAKEN_LINK_UP,
    )
    return Frame(MsgType.BEARING, dest=0xFF, src=ADDR, seq=0, payload=encode_bearing(report))


def test_streamed_bearing_is_ingested_and_marks_green(tmp_path: Path) -> None:
    master, link, _, _ = build(tmp_path)
    # The station streams a BEARING autonomously; the master never polls.
    link.b.send(_bearing().encode())
    master.step(20)  # now = 0.02 s

    stored = master.states[ADDR].last_bearing
    assert stored is not None
    assert abs(stored.bearing_deg - 123.4) < 0.05
    assert stored.power_dbm == -40.0
    assert master.health_snapshot(ADDR).state.value == "green"  # fresh, not yet established


def test_health_goes_red_when_the_stream_stops(tmp_path: Path) -> None:
    master, link, _, _ = build(tmp_path)
    link.b.send(_bearing().encode())
    master.step(20)
    assert master.health_snapshot(ADDR).state.value == "green"

    master.step(2000)  # 2.0 s later, no new bearing -> past the 1.0 s threshold
    assert master.health_snapshot(ADDR).state.value == "red"


def test_queued_field_edit_reaches_the_station_as_a_delta(tmp_path: Path) -> None:
    master, link, reader, _ = build(tmp_path)

    master.queue_delta(ADDR, {"uniform_gain": 9})
    master.step(0)

    frames = station_frames(link, reader)
    deltas = [f for f in frames if f.msg_type is MsgType.PARAM_DELTA]
    assert len(deltas) == 1
    assert decode_delta(deltas[0].payload) == {"uniform_gain": 9}
