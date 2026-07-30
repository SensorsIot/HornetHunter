"""Station agent tests (FSD §2.1, §5, §9, §10, §13) over an in-process link.

The station streams bearings autonomously (§5) and receives the master's
configuration traffic; it is never polled.
"""

from __future__ import annotations

from typing import Any

from hornethunter_kraken.agent import StationAgent
from hornethunter_kraken.doa_source import DoaSource, SyntheticSource
from hornethunter_kraken.settings_client import KrakenSettings
from hornethunter_shared.bearing import decode_bearing
from hornethunter_shared.carrier import InProcessLink
from hornethunter_shared.frame import Frame, FrameReader, MsgType
from hornethunter_shared.geo import LatLon
from hornethunter_shared.messages import FLAG_NO_DATA
from hornethunter_shared.protocol import AckPayload
from hornethunter_shared.registry import canonical_crc, encode_delta

MASTER = 0xFF
STATION = 5
REF = LatLon(47.0, 8.0)


class DeadSource(DoaSource):
    """A source that never yields a measurement and reports itself down."""

    def pump(self) -> None:
        return None


def _clock() -> float:
    return 1000.0


def _agent(link: InProcessLink, source: DoaSource, settings: KrakenSettings) -> StationAgent:
    return StationAgent(
        link.b,
        config={},
        source=source,
        settings=settings,
        address=STATION,
        reference=REF,
        clock=_clock,
    )


def _frames(link: InProcessLink) -> list[Frame]:
    return FrameReader().feed(link.a.recv())


def test_step_streams_a_decodable_bearing(
    full_settings: dict[str, Any], transport_factory: Any
) -> None:
    link = InProcessLink()
    settings = KrakenSettings(transport_factory(full_settings))
    source = SyntheticSource(clock=_clock, latitude=47.0, longitude=8.0)
    agent = _agent(link, source, settings)

    agent.step()  # streams autonomously — no poll

    bearings = [f for f in _frames(link) if f.msg_type == MsgType.BEARING]
    assert len(bearings) == 1
    report = decode_bearing(bearings[0].payload, station_id="kraken-07", reference=REF)
    assert report.has_data
    assert report.config_crc == canonical_crc(full_settings)


def test_param_delta_applied_once_and_acked_with_crc(
    full_settings: dict[str, Any], transport_factory: Any
) -> None:
    link = InProcessLink()
    transport = transport_factory(full_settings)
    settings = KrakenSettings(transport)
    agent = _agent(link, SyntheticSource(clock=_clock), settings)

    delta = encode_delta({"uniform_gain": 30})
    frame = Frame(MsgType.PARAM_DELTA, dest=STATION, src=MASTER, seq=9, payload=delta)

    link.a.send(frame.encode())
    agent.step()
    acks = [f for f in _frames(link) if f.msg_type == MsgType.ACK]
    assert len(acks) == 1
    parsed = AckPayload.decode(acks[0].payload)
    assert parsed.config_crc == canonical_crc({**full_settings, "uniform_gain": 30})
    assert transport.settings["uniform_gain"] == 30

    # Retransmission of the same frame: re-ACKed, applied only once (FR-10.7).
    writes_before = transport.writes
    link.a.send(frame.encode())
    agent.step()
    acks2 = [f for f in _frames(link) if f.msg_type == MsgType.ACK]
    assert len(acks2) == 1
    assert transport.writes == writes_before  # no second apply


def test_streams_no_data_when_source_unavailable(
    full_settings: dict[str, Any], transport_factory: Any
) -> None:
    link = InProcessLink()
    settings = KrakenSettings(transport_factory(full_settings))
    source = DeadSource()
    assert source.available is False
    agent = _agent(link, source, settings)

    agent.step()  # heartbeat stream even with no measurement (§9.6)

    bearings = [f for f in _frames(link) if f.msg_type == MsgType.BEARING]
    assert len(bearings) == 1
    report = decode_bearing(bearings[0].payload, station_id="kraken-07", reference=REF)
    assert not report.has_data
    assert report.flags & FLAG_NO_DATA
