"""Operator UI (FSD §14).

A Flask app on the Management Pi. All assets are served locally — no CDN, font,
tile or outbound request (A5) — and v1 is numeric only, no charts or maps
(N2, FR-14.2). The dashboard shows a numeric row per station (FR-14.1); a
WebSocket pushes live state (§14.2, NFR-14.1); settings panels are generated from
`FIELD_REGISTRY`, grouped per the §14.4 panel table (FR-14.3); a field edit posts a
delta, never a full set (FR-14.5); Read/Push full, Revert and a carrier pin are
per-station actions (FR-14.6–FR-14.8); a live log tail pane is served (FR-14.9);
and health and configuration state are separate indicators, each labelled with its
carrier (FR-14.10).

The server-side logic — state to JSON, form field to delta bytes, panel grouping —
is factored into free functions so it is host-testable with Flask's `test_client`
without a browser.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from flask import Flask, jsonify, render_template, request

from hornethunter_shared.registry import BY_KEY, FIELD_REGISTRY, FieldSpec, encode_delta

if TYPE_CHECKING:
    from .master import Master

# System-critical fields: warn on edit, never block (FR-14.4, §13.4).
WARN_FIELDS = frozenset({"doa_data_format", "default_ip", "data_interface"})

_VFO_SLOT = re.compile(r"^(vfo_.+)_(\d+)$")

# Explicit key -> panel mapping mirroring the KrakenSDR grouping (§14.4). Keys not
# listed fall through to "Recording / System" so every registry field is covered.
_PANEL_OF: dict[str, str] = {
    "center_freq": "RF Receiver",
    "uniform_gain": "RF Receiver",
    "data_interface": "RF Receiver",
    "default_ip": "RF Receiver",
    "en_doa": "DoA Configuration",
    "ant_arrangement": "DoA Configuration",
    "ula_direction": "DoA Configuration",
    "ant_spacing_meters": "DoA Configuration",
    "custom_array_x_meters": "DoA Configuration",
    "custom_array_y_meters": "DoA Configuration",
    "array_offset": "DoA Configuration",
    "doa_method": "DoA Configuration",
    "doa_decorrelation_method": "DoA Configuration",
    "expected_num_of_sources": "DoA Configuration",
    "doa_fig_type": "Display Options",
    "en_peak_hold": "Display Options",
    "compass_offset": "Display Options",
    "spectrum_calculation": "VFO Configuration",
    "vfo_mode": "VFO Configuration",
    "active_vfos": "VFO Configuration",
    "output_vfo": "VFO Configuration",
    "dsp_decimation": "VFO Configuration",
    "en_optimize_short_bursts": "VFO Configuration",
    "station_id": "Station Information",
    "location_source": "Station Information",
    "latitude": "Station Information",
    "longitude": "Station Information",
    "heading": "Station Information",
    "doa_data_format": "Station Information",
    "krakenpro_key": "Station Information",
    "rdf_mapper_server": "Station Information",
    "en_data_record": "Recording / System",
    "write_interval": "Recording / System",
    "logging_level": "Recording / System",
    "en_hw_check": "Recording / System",
    "disable_tooltips": "Recording / System",
}

_PANEL_ORDER = (
    "RF Receiver",
    "DoA Configuration",
    "Display Options",
    "VFO Configuration",
    "Station Information",
    "Recording / System",
)


def panel_for(key: str) -> str:
    """The §14.4 panel a settings key belongs to. Per-VFO families group into a
    `VFO N` panel; anything unlisted lands in Recording / System."""
    match = _VFO_SLOT.match(key)
    if match is not None:
        return f"VFO {match.group(2)}"
    return _PANEL_OF.get(key, "Recording / System")


@dataclass(frozen=True)
class PanelField:
    """One editable field on a panel, carrying its registry-derived metadata."""

    spec: FieldSpec
    warn: bool


@dataclass(frozen=True)
class Panel:
    """A named group of fields (§14.4)."""

    name: str
    fields: tuple[PanelField, ...]


def build_panels() -> list[Panel]:
    """Group the whole field registry into panels (FR-14.3). Every registry field
    appears in exactly one panel, so the form always covers the registry."""
    grouped: dict[str, list[PanelField]] = {}
    for spec in FIELD_REGISTRY:
        panel = panel_for(spec.key)
        grouped.setdefault(panel, []).append(PanelField(spec=spec, warn=spec.key in WARN_FIELDS))

    def sort_key(name: str) -> tuple[int, str]:
        if name in _PANEL_ORDER:
            return (_PANEL_ORDER.index(name), name)
        return (len(_PANEL_ORDER), name)

    return [Panel(name=name, fields=tuple(grouped[name])) for name in sorted(grouped, key=sort_key)]


def coerce_value(spec: FieldSpec, raw: Any) -> Any:
    """Coerce a submitted form value to the registry type before encoding
    (§14.5). Rejects out-of-type input (§14.5 failure mode)."""
    if spec.type == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "on", "yes")
    if spec.type == "enum":
        if str(raw) not in spec.enum_values:
            raise ValueError(f"{spec.key}: {raw!r} not one of {spec.enum_values}")
        return str(raw)
    if spec.type == "str":
        return str(raw)
    if spec.type == "fixed":
        return float(raw)
    return int(raw)


def field_to_delta(key: str, raw: Any) -> bytes:
    """Turn one submitted field into `PARAM_DELTA` bytes via the registry (FR-14.5).

    Raises KeyError for an unknown field and ValueError for an out-of-type value.
    """
    spec = BY_KEY[key]
    payload: bytes = encode_delta({key: coerce_value(spec, raw)})
    return payload


def station_payload(master: Master, addr: int) -> dict[str, Any]:
    """The numeric row for one station (FR-14.1), with health and configuration as
    separate carrier-labelled indicators (FR-14.10)."""
    state = master.states[addr]
    health = master.health_snapshot(addr)
    config = master.config_snapshot(addr)
    bearing = state.last_bearing
    wall_age_ms: float | None = None
    if state.last_bearing_wall is not None:
        wall_age_ms = (time.time() - state.last_bearing_wall) * 1000.0
    return {
        "addr": addr,
        "name": state.spec.name,
        "carrier": state.carrier.value,
        "pinned": None if state.pinned is None else state.pinned.value,
        "bearing_deg": None if bearing is None else round(bearing.bearing_deg, 2),
        "confidence": None if bearing is None else bearing.confidence,
        "power_dbm": None if bearing is None else bearing.power_dbm,
        "age_ms": None if bearing is None else bearing.age_ms,
        "wall_age_ms": None if wall_age_ms is None else round(wall_age_ms),
        "has_data": None if bearing is None else bearing.has_data,
        "discards": state.discards,
        "crc_failures": state.crc_failures,
        "health": {
            "state": health.state.value,
            "carrier": state.carrier.value,
            "time_since_last_s": (
                None if health.time_since_last_s is None else round(health.time_since_last_s, 2)
            ),
            "rate_hz": round(health.rate_hz, 2),
            "last_rssi_dbm": health.last_rssi_dbm,
        },
        "config": {
            "state": config.state.value,
            "carrier": state.carrier.value,
            "config_version": config.config_version,
            "expected_crc": config.expected_crc,
            "resynced": config.resynced,
            "latched": config.latched,
        },
    }


def state_payload(master: Master) -> dict[str, Any]:
    """The full dashboard state (§14.2), for the initial render and the WebSocket."""
    return {
        "stations": [station_payload(master, addr) for addr in sorted(master.states)],
        "ts": time.time(),
    }


class LogTail:
    """A bounded in-memory tail of the debugging log (FR-14.9). The real log is a
    rotating JSONL file (§20); this reads its last lines on demand."""

    def __init__(self, path: str | Path | None, max_lines: int = 200) -> None:
        self.path = Path(path) if path is not None else None
        self.max_lines = max_lines

    def lines(self, n: int | None = None) -> list[str]:
        count = min(n or self.max_lines, self.max_lines)
        if self.path is None or not self.path.is_file():
            return []
        tail: deque[str] = deque(maxlen=count)
        with self.path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.rstrip("\n")
                if stripped:
                    tail.append(stripped)
        return list(tail)


def create_app(master: Master, *, log_path: str | Path | None = None) -> Flask:
    """Build the Flask app bound to a running `Master`.

    Assets are inlined in the template (A5). The `/ws` WebSocket is registered when
    `flask_sock` is available; the HTTP routes work without it, which is what the
    host-tier `test_client` exercises.
    """
    here = Path(__file__).parent
    app = Flask(
        __name__,
        template_folder=str(here / "templates"),
        static_folder=str(here / "static"),
    )
    panels = build_panels()
    tail = LogTail(log_path)

    @app.get("/")
    def index() -> str:
        return render_template(
            "index.html",
            panels=panels,
            stations=[master.states[a].spec for a in sorted(master.states)],
            warn_fields=sorted(WARN_FIELDS),
        )

    @app.get("/api/state")
    def api_state() -> Any:
        return jsonify(state_payload(master))

    @app.post("/api/station/<int:addr>/field")
    def api_field(addr: int) -> Any:
        if addr not in master.states:
            return jsonify({"ok": False, "error": "unknown station"}), 404
        body = request.get_json(silent=True) or {}
        key = body.get("key")
        if not isinstance(key, str) or key not in BY_KEY:
            return jsonify({"ok": False, "error": "unknown field"}), 400
        try:
            spec = BY_KEY[key]
            value = coerce_value(spec, body.get("value"))
        except (ValueError, KeyError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        master.queue_delta(addr, {key: value})
        return jsonify(
            {"ok": True, "delta": field_to_delta(key, value).hex(), "warn": key in WARN_FIELDS}
        )

    @app.post("/api/station/<int:addr>/full_read")
    def api_full_read(addr: int) -> Any:
        if addr not in master.states:
            return jsonify({"ok": False, "error": "unknown station"}), 404
        master.request_full_read(addr)
        return jsonify({"ok": True})

    @app.post("/api/station/<int:addr>/full_push")
    def api_full_push(addr: int) -> Any:
        if addr not in master.states:
            return jsonify({"ok": False, "error": "unknown station"}), 404
        master.push_full(addr)
        return jsonify({"ok": True})

    @app.post("/api/station/<int:addr>/revert")
    def api_revert(addr: int) -> Any:
        if addr not in master.states:
            return jsonify({"ok": False, "error": "unknown station"}), 404
        restored = master.revert(addr)
        return jsonify({"ok": restored is not None})

    @app.post("/api/station/<int:addr>/pin")
    def api_pin(addr: int) -> Any:
        if addr not in master.states:
            return jsonify({"ok": False, "error": "unknown station"}), 404
        body = request.get_json(silent=True) or {}
        carrier_name = body.get("carrier")
        from .transport import CarrierKind

        carrier = None
        if carrier_name:
            try:
                carrier = CarrierKind(carrier_name)
            except ValueError:
                return jsonify({"ok": False, "error": "unknown carrier"}), 400
        master.pin_carrier(addr, carrier)
        return jsonify({"ok": True, "pinned": None if carrier is None else carrier.value})

    @app.get("/api/log/tail")
    def api_log_tail() -> Any:
        n = request.args.get("n", type=int)
        return jsonify({"lines": tail.lines(n)})

    _register_websocket(app, master)
    return app


def _register_websocket(app: Flask, master: Master) -> None:
    """Register the live-state WebSocket if `flask_sock` is installed (§14.2)."""
    try:
        from flask_sock import Sock  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover - host tests do not require the socket
        return

    sock = Sock(app)

    @sock.route("/ws")  # type: ignore[untyped-decorator]
    def ws(connection: Any) -> None:  # pragma: no cover - needs a live socket
        import json

        while True:
            connection.send(json.dumps(state_payload(master)))
            time.sleep(0.2)  # push within 250 ms of arrival (NFR-14.1)
