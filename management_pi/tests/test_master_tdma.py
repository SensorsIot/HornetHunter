"""Master TDMA tests (FSD §5, §6): beacon emission, slot-map, JOIN, config-window."""

from __future__ import annotations

from pathlib import Path

from _settings import full_settings

from hornethunter_management.master import Master, MasterConfig, StationSpec
from hornethunter_management.mirror import ConfigMirror
from hornethunter_shared.carrier import InProcessLink
from hornethunter_shared.frame import Frame, FrameReader, MsgType
from hornethunter_shared.protocol import BeaconPayload


def _join(src: int) -> bytes:
    return Frame(MsgType.JOIN, dest=0xFF, src=src, seq=0).encode()


def _join_with_ref(src: int, lat: float, lon: float) -> bytes:
    from hornethunter_shared.protocol import JoinPayload

    payload = JoinPayload(nonce=src, ref_lat=lat, ref_lon=lon).encode()
    return Frame(MsgType.JOIN, dest=0xFF, src=src, seq=0, payload=payload).encode()


def build(tmp_path: Path, *, enabled: bool = True) -> tuple[Master, InProcessLink, FrameReader]:
    link = InProcessLink()
    mirror = ConfigMirror(tmp_path / "mirror.json")
    mirror.seed(1, full_settings(uniform_gain=1), version=0)
    config = MasterConfig(
        stations=(StationSpec(1, "s1"), StationSpec(2, "s2"), StationSpec(3, "s3")),
        tdma_enabled=enabled,
        superframe_period_ms=1000,
        superframe_slot_ms=125,
        tdma_staleness_ms=3000,
    )
    return Master(link.a, config, mirror), link, FrameReader()


def _beacons(link: InProcessLink, reader: FrameReader) -> list[BeaconPayload]:
    return [
        BeaconPayload.decode(f.payload)
        for f in reader.feed(link.b.recv())
        if f.msg_type is MsgType.BEACON
    ]


def test_unconfigured_station_self_admits_on_join(tmp_path: Path) -> None:
    # A master with NO configured stations admits whoever announces itself (§5.3).
    link = InProcessLink()
    mirror = ConfigMirror(tmp_path / "mirror.json")
    master = Master(
        link.a,
        MasterConfig(stations=(), tdma_enabled=True, superframe_period_ms=1000,
                     superframe_slot_ms=125, tdma_staleness_ms=3000),
        mirror,
    )
    link.b.send(_join_with_ref(7, 47.3769, 8.5417))
    master.step(10)
    assert 7 in master.states  # admitted by number, no config entry
    ref = master.states[7].spec.reference
    assert ref is not None and abs(ref.lat - 47.3769) < 1e-6  # learned from the JOIN
    beacons = _beacons(link, FrameReader())
    assert beacons and 7 in beacons[0].slots  # and it gets a data slot


def test_beacon_carries_live_slot_map(tmp_path: Path) -> None:
    master, link, reader = build(tmp_path)
    link.b.send(_join(1))
    link.b.send(_join(2))
    master.step(10)  # process joins, emit first beacon
    beacons = _beacons(link, reader)
    assert len(beacons) == 1
    assert beacons[0].slots == (1, 2, 1, 2, 0, 0)  # 2 live, 6 data slots, cap 2


def test_beacon_cadence_one_per_superframe(tmp_path: Path) -> None:
    master, link, reader = build(tmp_path)
    master.step(10)    # first beacon (immediate)
    master.step(500)   # within the 1000 ms period -> none
    master.step(1100)  # next superframe -> beacon
    assert len(_beacons(link, reader)) == 2


def test_no_beacon_when_disabled(tmp_path: Path) -> None:
    master, link, reader = build(tmp_path, enabled=False)
    master.step(10)
    master.step(1100)
    assert _beacons(link, reader) == []


def test_join_then_stale_departure_compacts(tmp_path: Path) -> None:
    master, link, reader = build(tmp_path)
    link.b.send(_join(2))
    link.b.send(_join(3))
    master.step(10)
    assert _beacons(link, reader)[0].slots == (2, 3, 2, 3, 0, 0)  # 1 never joined
    master.step(4000)  # both go stale (>3 s) -> empty map
    assert _beacons(link, reader)[-1].slots == (0, 0, 0, 0, 0, 0)


def test_beacon_flags_config_target(tmp_path: Path) -> None:
    master, link, reader = build(tmp_path)
    link.b.send(_join(1))
    master.step(10)  # join + first beacon (nothing pending)
    reader.feed(link.b.recv())  # drain
    master.queue_delta(1, {"uniform_gain": 0.5})
    master.step(1100)  # next superframe: config now pending
    beacons = _beacons(link, reader)
    assert beacons and beacons[-1].config_target == 1
