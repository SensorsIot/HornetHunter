from pathlib import Path

from _settings import full_settings

from hornethunter_management.master import Master, MasterConfig, StationSpec
from hornethunter_management.mirror import ConfigMirror
from hornethunter_management.ui import build_panels, create_app, field_to_delta, state_payload
from hornethunter_shared.carrier import InProcessLink
from hornethunter_shared.registry import FIELD_REGISTRY, encode_delta

ADDR = 1


def build_master(tmp_path: Path) -> Master:
    link = InProcessLink()
    mirror = ConfigMirror(tmp_path / "mirror.json")
    mirror.seed(ADDR, full_settings(uniform_gain=1), version=0)
    config = MasterConfig(stations=(StationSpec(addr=ADDR, name="s1"),))
    return Master(link.a, config, mirror)


def test_panels_cover_every_registry_field() -> None:
    covered = {f.spec.key for panel in build_panels() for f in panel.fields}
    assert covered == {spec.key for spec in FIELD_REGISTRY}


def test_warn_fields_are_flagged_in_panels() -> None:
    warned = {f.spec.key for panel in build_panels() for f in panel.fields if f.warn}
    assert warned == {"doa_data_format", "default_ip", "data_interface"}


def test_field_to_delta_matches_the_registry_encoding() -> None:
    assert field_to_delta("uniform_gain", "7") == encode_delta({"uniform_gain": 7})
    assert field_to_delta("en_doa", "true") == encode_delta({"en_doa": True})
    assert field_to_delta("doa_method", "MUSIC") == encode_delta({"doa_method": "MUSIC"})


def test_dashboard_renders_station_rows(tmp_path: Path) -> None:
    app = create_app(build_master(tmp_path))
    html = app.test_client().get("/").get_data(as_text=True)
    assert "s1" in html
    assert "Stations" in html
    assert "RF Receiver" in html  # a generated panel
    assert "uniform_gain" in html  # a generated field


def test_served_html_has_no_outbound_urls(tmp_path: Path) -> None:
    app = create_app(build_master(tmp_path))
    html = app.test_client().get("/").get_data(as_text=True)
    for needle in ("http://", "https://", "cdn", "googleapis", "unpkg", "jsdelivr"):
        assert needle not in html


def test_state_endpoint_reports_separate_health_and_config(tmp_path: Path) -> None:
    app = create_app(build_master(tmp_path))
    payload = app.test_client().get("/api/state").get_json()
    row = payload["stations"][0]
    assert row["name"] == "s1"
    assert "state" in row["health"] and "carrier" in row["health"]
    assert "state" in row["config"] and "carrier" in row["config"]


def test_field_post_queues_a_delta(tmp_path: Path) -> None:
    master = build_master(tmp_path)
    app = create_app(master)
    resp = app.test_client().post(
        f"/api/station/{ADDR}/field", json={"key": "uniform_gain", "value": "7"}
    )
    body = resp.get_json()
    assert body["ok"]
    assert body["delta"] == encode_delta({"uniform_gain": 7}).hex()


def test_field_post_warns_on_system_critical(tmp_path: Path) -> None:
    master = build_master(tmp_path)
    app = create_app(master)
    resp = app.test_client().post(
        f"/api/station/{ADDR}/field", json={"key": "doa_data_format", "value": "Kraken Pro Local"}
    )
    assert resp.get_json()["warn"] is True


def test_field_post_rejects_out_of_type_enum(tmp_path: Path) -> None:
    master = build_master(tmp_path)
    app = create_app(master)
    resp = app.test_client().post(
        f"/api/station/{ADDR}/field", json={"key": "doa_method", "value": "NOPE"}
    )
    assert resp.status_code == 400


def test_pin_endpoint_sets_carrier(tmp_path: Path) -> None:
    master = build_master(tmp_path)
    app = create_app(master)
    resp = app.test_client().post(f"/api/station/{ADDR}/pin", json={"carrier": "wlan"})
    assert resp.get_json()["pinned"] == "wlan"
    assert master.states[ADDR].pinned is not None


def test_state_payload_is_json_serialisable(tmp_path: Path) -> None:
    import json

    json.dumps(state_payload(build_master(tmp_path)))
