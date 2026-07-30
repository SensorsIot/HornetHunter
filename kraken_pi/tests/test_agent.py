"""Station agent tests (FSD §2.1, §9, §10, §13) over an in-process link."""

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
from hornethunter_shared.protocol import AckPayload, PollPayload
from hornethunter_shared.registry import canonical_crc, encode_delta

MASTER = 1
STATION = 5
SLOT = 1
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
        slot_index=SLOT,
        address=STATION,
        reference=REF,
        clock=_clock,
    )


def _poll(expected_bit: int, seq: int = 7) -> bytes:
    payload = PollPayload(cycle_seq=1, slot_ms=150, expected=expected_bit).encode()
    return Frame(MsgType.POLL, dest=STATION, src=MASTER, seq=seq, payload=payload).encode()


def _first_frame(link: InProcessLink) -> Frame | None:
    frames = FrameReader().feed(link.a.recv())
    return frames[0] if frames else None


def test_poll_for_our_slot_yields_a_decodable_bearing(
    full_settings: dict[str, Any], transport_factory: Any
) -> None:
    link = InProcessLink()
    settings = KrakenSettings(transport_factory(full_settings))
    source = SyntheticSource(clock=_clock, latitude=47.0, longitude=8.0)
    agent = _agent(link, source, settings)

    link.a.send(_poll(1 << SLOT))
    agent.step()

    frame = _first_frame(link)
    assert frame is not None
    assert frame.msg_type == MsgType.BEARING
    report = decode_bearing(frame.payload, station_id="kraken-07", reference=REF)
    assert report.has_data
    assert report.config_crc == canonical_crc(full_settings)


def test_poll_for_another_slot_is_ignored(
    full_settings: dict[str, Any], transport_factory: Any
) -> None:
    link = InProcessLink()
    settings = KrakenSettings(transport_factory(full_settings))
    agent = _agent(link, SyntheticSource(clock=_clock), settings)

    link.a.send(_poll(1 << (SLOT + 1)))  # a different station's slot
    agent.step()
    assert _first_frame(link) is None


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
    ack = _first_frame(link)
    assert ack is not None and ack.msg_type == MsgType.ACK
    parsed = AckPayload.decode(ack.payload)
    expected_crc = canonical_crc({**full_settings, "uniform_gain": 30})
    assert parsed.config_crc == expected_crc
    assert transport.settings["uniform_gain"] == 30

    # Retransmission of the same frame: re-ACKed, applied only once (FR-10.7).
    writes_before = transport.writes
    link.a.send(frame.encode())
    agent.step()
    ack2 = _first_frame(link)
    assert ack2 is not None and ack2.msg_type == MsgType.ACK
    assert transport.writes == writes_before  # no second apply


def test_answers_polls_with_no_data_when_source_unavailable(
    full_settings: dict[str, Any], transport_factory: Any
) -> None:
    link = InProcessLink()
    settings = KrakenSettings(transport_factory(full_settings))
    source = DeadSource()
    assert source.available is False
    agent = _agent(link, source, settings)

    link.a.send(_poll(1 << SLOT))
    agent.step()

    frame = _first_frame(link)
    assert frame is not None
    assert frame.msg_type == MsgType.BEARING
    report = decode_bearing(frame.payload, station_id="kraken-07", reference=REF)
    assert not report.has_data
    assert report.flags & FLAG_NO_DATA
