"""End-to-end TDMA (FSD §5, §6): a real Master and KrakenProxy over one link.

Drives both off a single millisecond clock: the master beacons, the station hears
it, JOINs when unslotted, and once the master slots it, transmits its bearing in its
slot — where the master receives it and marks the station GREEN. Nothing here is
free-running; a bearing only reaches the master through the full TDMA path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hornethunter_kraken.agent import KrakenProxy
from hornethunter_kraken.doa_source import SyntheticSource
from hornethunter_kraken.settings_client import KrakenSettings
from hornethunter_management.master import Master, MasterConfig, StationSpec
from hornethunter_management.mirror import ConfigMirror
from hornethunter_shared.carrier import InProcessLink
from hornethunter_shared.geo import LatLon

STATION = 5


def test_tdma_full_loop_join_and_slotted_bearing(
    tmp_path: Path, full_settings: dict[str, Any], transport_factory: Any
) -> None:
    link = InProcessLink()
    mirror = ConfigMirror(tmp_path / "mirror.json")
    mirror.seed(STATION, full_settings, version=0)
    master = Master(
        link.a,
        MasterConfig(
            stations=(StationSpec(STATION, "s5"),),
            tdma_enabled=True,
            superframe_period_ms=1000,
            superframe_slot_ms=125,
            staleness_threshold_s=5.0,
            tdma_staleness_ms=3000,
        ),
        mirror,
    )
    station = KrakenProxy(
        link.b,
        config={"tdma": {"enabled": True, "period_ms": 1000, "slot_ms": 125, "guard_ms": 25}},
        source=SyntheticSource(clock=lambda: 0.0, latitude=47.0, longitude=8.0),
        settings=KrakenSettings(transport_factory(full_settings)),
        address=STATION,
        reference=LatLon(47.0, 8.0),
        clock=lambda: 0.0,
        join_rng=lambda: 0.5,
    )

    # One shared clock (ms) across ~3 superframes.
    for t in range(0, 3001, 20):
        master.step(t)
        station.step(now=t / 1000.0)

    assert STATION in master.states
    assert master.states[STATION].last_bearing is not None  # a slotted bearing arrived
    assert master.health_snapshot(STATION).state.value == "green"
    # the master learned the station is live purely from the TDMA traffic
    assert STATION in master.scheduler.live(3000)
