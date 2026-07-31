"""KrakenProxy TDMA tests (FSD §5, §6): beacon sync, slot-gated tx, JOIN, beacon-loss."""

from __future__ import annotations

from typing import Any

from hornethunter_kraken.agent import KrakenProxy
from hornethunter_kraken.doa_source import SyntheticSource
from hornethunter_kraken.settings_client import KrakenSettings
from hornethunter_shared.carrier import InProcessLink
from hornethunter_shared.frame import Frame, FrameReader, MsgType
from hornethunter_shared.geo import LatLon
from hornethunter_shared.protocol import BeaconPayload

REF = LatLon(47.0, 8.0)
STATION = 5


def _clock() -> float:
    return 0.0


def _tdma_agent(link: InProcessLink, source: Any, settings: KrakenSettings) -> KrakenProxy:
    return KrakenProxy(
        link.b,
        config={"tdma": {"enabled": True, "period_ms": 1000, "slot_ms": 125, "guard_ms": 25}},
        source=source,
        settings=settings,
        address=STATION,
        reference=REF,
        clock=_clock,
        join_rng=lambda: 0.5,  # deterministic: offset ~ middle of the join window
    )


def _source() -> SyntheticSource:
    return SyntheticSource(clock=_clock, latitude=47.0, longitude=8.0)


def _send_beacon(link: InProcessLink, seq: int, slots: tuple[int, ...]) -> None:
    payload = BeaconPayload(seq=seq, slots=slots).encode()
    link.a.send(Frame(MsgType.BEACON, dest=0xFF, src=0xFF, seq=0, payload=payload).encode())


def _sent(link: InProcessLink) -> list[Frame]:
    return FrameReader().feed(link.a.recv())


def test_transmits_only_in_assigned_slot(full_settings: Any, transport_factory: Any) -> None:
    link = InProcessLink()
    agent = _tdma_agent(link, _source(), KrakenSettings(transport_factory(full_settings)))
    # station 5 owns data slot 0 → window [275, 350] ms after the beacon
    _send_beacon(link, seq=1, slots=(5, 0, 0, 0, 0, 0))
    agent.step(now=0.0)  # hear the beacon (t0 = 0)
    _sent(link)
    agent.step(now=0.10)  # 100 ms: before the slot → nothing
    assert not [f for f in _sent(link) if f.msg_type == MsgType.BEARING]
    agent.step(now=0.30)  # 300 ms: inside slot 0 → one bearing
    bearings = [f for f in _sent(link) if f.msg_type == MsgType.BEARING]
    assert len(bearings) == 1 and bearings[0].src == STATION
    agent.step(now=0.32)  # still in window, already sent → no second
    assert not [f for f in _sent(link) if f.msg_type == MsgType.BEARING]


def test_unslotted_station_sends_join(full_settings: Any, transport_factory: Any) -> None:
    link = InProcessLink()
    agent = _tdma_agent(link, _source(), KrakenSettings(transport_factory(full_settings)))
    _send_beacon(link, seq=1, slots=(1, 0, 0, 0, 0, 0))  # station 5 not in the map
    agent.step(now=0.0)
    _sent(link)
    agent.step(now=0.20)  # join window [150,225] ms, offset ~187 → JOIN
    joins = [f for f in _sent(link) if f.msg_type == MsgType.JOIN]
    assert len(joins) == 1 and joins[0].src == STATION
    agent.step(now=0.21)  # already joined this superframe → no second JOIN
    assert not [f for f in _sent(link) if f.msg_type == MsgType.JOIN]


def test_no_beacon_no_transmit(full_settings: Any, transport_factory: Any) -> None:
    link = InProcessLink()
    agent = _tdma_agent(link, _source(), KrakenSettings(transport_factory(full_settings)))
    agent.step(now=0.30)  # no beacon ever heard → never transmit blind (FR-5.2)
    assert _sent(link) == []


def test_beacon_loss_goes_silent(full_settings: Any, transport_factory: Any) -> None:
    link = InProcessLink()
    agent = _tdma_agent(link, _source(), KrakenSettings(transport_factory(full_settings)))
    _send_beacon(link, seq=1, slots=(5, 0, 0, 0, 0, 0))
    agent.step(now=0.0)
    agent.step(now=0.30)  # transmits in its slot
    _sent(link)
    agent.step(now=2.5)  # >2*period since the last beacon → go silent (FR-5.7)
    assert not [f for f in _sent(link) if f.msg_type == MsgType.BEARING]


def test_new_superframe_allows_retransmit(full_settings: Any, transport_factory: Any) -> None:
    link = InProcessLink()
    agent = _tdma_agent(link, _source(), KrakenSettings(transport_factory(full_settings)))
    _send_beacon(link, seq=1, slots=(5, 0, 0, 0, 0, 0))
    agent.step(now=0.0)
    agent.step(now=0.30)  # slot tx in superframe 1
    _sent(link)
    _send_beacon(link, seq=2, slots=(5, 0, 0, 0, 0, 0))  # next superframe
    agent.step(now=1.0)  # hear the new beacon (t0 = 1.0), resets the sent-slot set
    agent.step(now=1.30)  # 300 ms in → transmit again
    assert len([f for f in _sent(link) if f.msg_type == MsgType.BEARING]) == 1
