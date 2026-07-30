from hornethunter_shared.registry import (
    BY_KEY,
    FIELD_REGISTRY,
    canonical_crc,
    decode_delta,
    encode_delta,
)

# A full, plausible settings dict covering every registry key.
SETTINGS = {
    "center_freq": 148.524,
    "uniform_gain": 20,
    "data_interface": "eth0",
    "default_ip": "0.0.0.0",
    "en_doa": True,
    "ant_arrangement": "ULA",
    "ula_direction": "Both",
    "ant_spacing_meters": 0.53,
    "array_offset": 0,
    "doa_method": "MUSIC",
    "doa_decorrelation_method": "FBA",
    "expected_num_of_sources": 1,
    "doa_fig_type": "Compass",
    "en_peak_hold": False,
    "compass_offset": 0.0,
    "spectrum_calculation": "Single",
    "vfo_mode": "Standard",
    "active_vfos": 1,
    "output_vfo": 0,
    "dsp_decimation": 1,
    "en_optimize_short_bursts": False,
    "vfo_freq_0": 148_524_000.0,
    "vfo_bw_0": 12_500.0,
    "vfo_squelch_0": -60,
    "vfo_squelch_mode_0": "Default",
    "vfo_demod_0": "Default",
    "station_id": "hb9bla-st4",
    "location_source": "gpsd",
    "latitude": 47.3769,
    "longitude": 8.5417,
    "heading": 0.0,
    "doa_data_format": "Kraken Pro Local",
    "ext_upd_flag": 0,
}


def test_registry_ids_are_unique_and_stable() -> None:
    ids = [spec.id for spec in FIELD_REGISTRY]
    assert len(ids) == len(set(ids))
    assert all(key in {s.key for s in FIELD_REGISTRY} for key in SETTINGS)


def test_delta_round_trip_mixed_types() -> None:
    changes = {
        "center_freq": 148.6,
        "uniform_gain": 25,
        "en_doa": False,
        "doa_method": "Capon",
        "station_id": "st-7",
        "vfo_squelch_0": -55,
    }
    restored = decode_delta(encode_delta(changes))
    assert restored["uniform_gain"] == 25
    assert restored["en_doa"] is False
    assert restored["doa_method"] == "Capon"
    assert restored["station_id"] == "st-7"
    assert restored["vfo_squelch_0"] == -55
    assert abs(restored["center_freq"] - 148.6) < 1e-6


def test_single_field_delta_fits_one_frame() -> None:
    # NFR-7.1: a single-field delta is tiny.
    assert len(encode_delta({"center_freq": 148.6})) <= 8


def test_canonical_crc_is_order_independent() -> None:
    shuffled = dict(reversed(list(SETTINGS.items())))
    assert canonical_crc(SETTINGS) == canonical_crc(shuffled)


def test_canonical_crc_unaffected_by_gps_mutated_fields() -> None:
    moved = dict(SETTINGS)
    moved["latitude"] = 47.9999  # crc_covered = False
    moved["longitude"] = 8.9999
    moved["heading"] = 42.0
    moved["ext_upd_flag"] = 1
    assert canonical_crc(moved) == canonical_crc(SETTINGS)


def test_canonical_crc_changes_on_covered_field() -> None:
    changed = dict(SETTINGS)
    changed["center_freq"] = 149.0
    assert canonical_crc(changed) != canonical_crc(SETTINGS)


def test_every_covered_field_is_encodable() -> None:
    # Guards against a registry row whose sample value cannot be encoded.
    for spec in FIELD_REGISTRY:
        if spec.crc_covered:
            assert BY_KEY[spec.key].encode_value(SETTINGS[spec.key]) is not None
