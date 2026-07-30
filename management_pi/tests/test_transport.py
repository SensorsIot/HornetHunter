import pytest

from hornethunter_management.transport import CarrierKind, TransportSelector

STATION = 1


def sel() -> TransportSelector:
    return TransportSelector(promote_probes=3, demote_probes=2, dwell_s=30.0)


def test_starts_on_lora() -> None:
    assert sel().active(STATION) is CarrierKind.LORA


def test_promotion_needs_consecutive_successes() -> None:
    s = sel()
    assert s.on_probe(STATION, True, 0).active is CarrierKind.LORA
    assert s.on_probe(STATION, True, 1000).active is CarrierKind.LORA
    result = s.on_probe(STATION, True, 2000)
    assert result.active is CarrierKind.WLAN
    assert result.changed


def test_a_failure_resets_the_success_run() -> None:
    s = sel()
    s.on_probe(STATION, True, 0)
    s.on_probe(STATION, True, 1000)
    s.on_probe(STATION, False, 2000)  # resets
    s.on_probe(STATION, True, 3000)
    s.on_probe(STATION, True, 4000)
    assert s.active(STATION) is CarrierKind.LORA  # only two in a row since reset


def _promote(s: TransportSelector, base: int = 0) -> None:
    for i in range(3):
        s.on_probe(STATION, True, base + i * 1000)


def test_demotion_is_faster_than_promotion() -> None:
    s = sel()
    _promote(s)  # now WLAN at t=2000, last switch 2000
    # dwell is 30s; failures accumulate but cannot switch until dwell elapses
    s.on_probe(STATION, False, 40000)
    result = s.on_probe(STATION, False, 41000)  # two failures, dwell satisfied
    assert result.active is CarrierKind.LORA
    assert result.changed


def test_dwell_blocks_an_immediate_switch_back() -> None:
    s = sel()
    _promote(s)  # WLAN at t=2000
    s.on_probe(STATION, False, 3000)
    s.on_probe(STATION, False, 4000)  # dwell (30s) not yet elapsed
    assert s.active(STATION) is CarrierKind.WLAN


def test_pin_disables_auto_selection() -> None:
    s = sel()
    s.set_pin(STATION, CarrierKind.LORA)
    for i in range(5):
        result = s.on_probe(STATION, True, i * 10000)  # would otherwise promote
        assert result.active is CarrierKind.LORA
        assert not result.changed
    assert s.pin(STATION) is CarrierKind.LORA
    s.set_pin(STATION, None)
    assert s.pin(STATION) is None


def test_per_station_state_is_independent() -> None:
    s = sel()
    _promote(s)  # station 1 -> WLAN
    assert s.active(1) is CarrierKind.WLAN
    assert s.active(2) is CarrierKind.LORA  # untouched


def test_demote_must_be_less_than_promote() -> None:
    with pytest.raises(ValueError):
        TransportSelector(promote_probes=2, demote_probes=2)
