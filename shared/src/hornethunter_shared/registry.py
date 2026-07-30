"""KrakenSDR settings field registry and configuration codecs (FSD §7.2–§7.4).

The registry is data: one entry per settings key, with a stable numeric id and a
declared wire type. It drives three things:

* `encode_delta` / `decode_delta` — the `PARAM_DELTA` payload (only changed fields).
* `canonical_crc` — a CRC over the operator-owned settings, computed from encoded
  *values* rather than the `settings.json` bytes, so it is identical on both nodes
  and stable across the KrakenSDR's own float re-serialisation (§7.4).
* the Management UI form (field types, units, ranges come from here, not hand-code).

This table is a representative subset generated from a live station's settings; it
is extended by adding rows, never by renumbering existing ids.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

from .crc import crc16_ccitt_false

_PACK: dict[str, struct.Struct] = {
    "u8": struct.Struct(">B"),
    "u16": struct.Struct(">H"),
    "u32": struct.Struct(">I"),
    "i8": struct.Struct(">b"),
    "i16": struct.Struct(">h"),
    "i32": struct.Struct(">i"),
    "fixed": struct.Struct(">i"),
}


@dataclass(frozen=True)
class FieldSpec:
    """One settings field. `type` selects the wire encoding; `scale` applies to
    `fixed` reals; `crc_covered` gates participation in the canonical CRC."""

    id: int
    key: str
    type: str
    scale: int = 1
    crc_covered: bool = True
    unit: str = ""
    enum_values: tuple[str, ...] = ()

    def encode_value(self, value: Any) -> bytes:
        if self.type == "bool":
            return b"\x01" if value else b"\x00"
        if self.type == "enum":
            return bytes((self.enum_values.index(str(value)),))
        if self.type == "str":
            raw = str(value).encode("utf-8")
            if len(raw) > 0xFF:
                raise ValueError(f"{self.key} string exceeds 255 bytes")
            return bytes((len(raw),)) + raw
        if self.type == "fixed":
            return _PACK["fixed"].pack(round(float(value) * self.scale))
        return _PACK[self.type].pack(int(value))

    def decode_value(self, data: bytes) -> tuple[Any, int]:
        if self.type == "bool":
            return bool(data[0]), 1
        if self.type == "enum":
            return self.enum_values[data[0]], 1
        if self.type == "str":
            length = data[0]
            return data[1 : 1 + length].decode("utf-8"), 1 + length
        if self.type == "fixed":
            (raw,) = _PACK["fixed"].unpack(data[:4])
            return raw / self.scale, 4
        packer = _PACK[self.type]
        (value,) = packer.unpack(data[: packer.size])
        return value, packer.size


# id, key, type, scale, crc_covered, unit, enum_values
FIELD_REGISTRY: tuple[FieldSpec, ...] = (
    FieldSpec(1, "center_freq", "fixed", 1_000_000, unit="MHz"),
    FieldSpec(2, "uniform_gain", "u8", unit="dB"),
    FieldSpec(3, "data_interface", "str"),
    FieldSpec(4, "default_ip", "str"),
    FieldSpec(5, "en_doa", "bool"),
    FieldSpec(6, "ant_arrangement", "enum", enum_values=("ULA", "UCA", "Custom")),
    FieldSpec(7, "ula_direction", "enum", enum_values=("Both", "Forward", "Backward")),
    FieldSpec(8, "ant_spacing_meters", "fixed", 1000, unit="m"),
    FieldSpec(9, "array_offset", "i16", unit="deg"),
    FieldSpec(10, "doa_method", "enum", enum_values=("Bartlett", "Capon", "MEM", "MUSIC")),
    FieldSpec(11, "doa_decorrelation_method", "enum",
              enum_values=("Off", "FBA", "TOEP", "FBSS")),
    FieldSpec(12, "expected_num_of_sources", "u8"),
    FieldSpec(13, "doa_fig_type", "enum", enum_values=("Linear", "Polar", "Compass")),
    FieldSpec(14, "en_peak_hold", "bool"),
    FieldSpec(15, "compass_offset", "fixed", 10, unit="deg"),
    FieldSpec(16, "spectrum_calculation", "enum", enum_values=("Single", "Continuous")),
    FieldSpec(17, "vfo_mode", "enum", enum_values=("Standard", "VFO-0 Auto Squelch")),
    FieldSpec(18, "active_vfos", "u8"),
    FieldSpec(19, "output_vfo", "i8"),
    FieldSpec(20, "dsp_decimation", "u16"),
    FieldSpec(21, "en_optimize_short_bursts", "bool"),
    FieldSpec(22, "vfo_freq_0", "fixed", 1, unit="Hz"),
    FieldSpec(23, "vfo_bw_0", "fixed", 1, unit="Hz"),
    FieldSpec(24, "vfo_squelch_0", "i16", unit="dB"),
    FieldSpec(25, "vfo_squelch_mode_0", "enum", enum_values=("Default", "Manual", "Auto")),
    FieldSpec(26, "vfo_demod_0", "enum", enum_values=("None", "FM", "AM")),
    FieldSpec(27, "station_id", "str"),
    FieldSpec(28, "location_source", "enum", enum_values=("None", "Static", "gpsd")),
    FieldSpec(29, "latitude", "fixed", 1_000_000, crc_covered=False, unit="deg"),
    FieldSpec(30, "longitude", "fixed", 1_000_000, crc_covered=False, unit="deg"),
    FieldSpec(31, "heading", "fixed", 10, crc_covered=False, unit="deg"),
    FieldSpec(32, "doa_data_format", "enum",
              enum_values=("Kraken App", "Kraken Pro Local", "Kraken Pro Remote",
                           "DF Aggregator", "RDF Mapper", "Full POST")),
    FieldSpec(33, "ext_upd_flag", "u8", crc_covered=False),
)

BY_ID: dict[int, FieldSpec] = {spec.id: spec for spec in FIELD_REGISTRY}
BY_KEY: dict[str, FieldSpec] = {spec.key: spec for spec in FIELD_REGISTRY}


def encode_delta(changes: dict[str, Any]) -> bytes:
    """Encode changed fields as `id · value` pairs, ordered by id for determinism."""
    out = bytearray()
    for spec in sorted((BY_KEY[key] for key in changes), key=lambda s: s.id):
        out += bytes((spec.id,)) + spec.encode_value(changes[spec.key])
    return bytes(out)


def decode_delta(data: bytes) -> dict[str, Any]:
    """Inverse of `encode_delta`."""
    result: dict[str, Any] = {}
    offset = 0
    while offset < len(data):
        spec = BY_ID[data[offset]]
        offset += 1
        value, consumed = spec.decode_value(data[offset:])
        result[spec.key] = value
        offset += consumed
    return result


def canonical_crc(settings: dict[str, Any]) -> int:
    """CRC-16 over the crc-covered fields, in id order, from encoded values (§7.4).

    Fields the KrakenSDR mutates on its own (position, heading, bookkeeping) are
    excluded by `crc_covered=False`, so the CRC is stable across passes. A covered
    field absent from `settings` raises KeyError, surfacing an incomplete read.
    """
    buf = bytearray()
    for spec in FIELD_REGISTRY:
        if not spec.crc_covered:
            continue
        buf += spec.encode_value(settings[spec.key])
    return crc16_ccitt_false(bytes(buf))
