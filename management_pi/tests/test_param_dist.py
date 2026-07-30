from pathlib import Path

import pytest
from _settings import full_settings

from hornethunter_management.mirror import ConfigMirror
from hornethunter_management.param_dist import (
    ConfigState,
    NoBaselineError,
    ParameterDistributor,
    encode_full,
)
from hornethunter_shared.registry import canonical_crc, decode_delta

ADDR = 1


def seeded(tmp_path: Path) -> tuple[ConfigMirror, ParameterDistributor]:
    mirror = ConfigMirror(tmp_path / "mirror.json")
    mirror.seed(ADDR, full_settings(uniform_gain=1), version=0)
    return mirror, ParameterDistributor(mirror)


def test_prepare_delta_encodes_only_changed_fields(tmp_path: Path) -> None:
    mirror, pd = seeded(tmp_path)
    push = pd.prepare_delta(ADDR, {"uniform_gain": 7})
    assert push is not None
    assert push.kind == "delta"
    assert decode_delta(push.payload) == {"uniform_gain": 7}
    assert push.version == 1
    assert push.expected_crc == canonical_crc(full_settings(uniform_gain=7))


def test_prepare_delta_no_change_returns_none(tmp_path: Path) -> None:
    _, pd = seeded(tmp_path)
    assert pd.prepare_delta(ADDR, {"uniform_gain": 1}) is None


def test_prepare_delta_without_baseline_raises(tmp_path: Path) -> None:
    pd = ParameterDistributor(ConfigMirror(tmp_path / "mirror.json"))
    with pytest.raises(NoBaselineError):
        pd.prepare_delta(ADDR, {"uniform_gain": 7})


def test_matching_ack_commits_to_mirror(tmp_path: Path) -> None:
    mirror, pd = seeded(tmp_path)
    push = pd.prepare_delta(ADDR, {"uniform_gain": 7})
    assert push is not None
    result = pd.on_ack(ADDR, push.version, push.expected_crc)
    assert result.committed
    assert result.state is ConfigState.IN_SYNC
    assert mirror.current(ADDR)["uniform_gain"] == 7
    assert mirror.version(ADDR) == 1


def test_full_set_encoding_respects_active_vfos(tmp_path: Path) -> None:
    # active_vfos = 0 -> the vfo_*_0 family is dropped (FR-7.10).
    _, changes0 = encode_full(full_settings(active_vfos=0))
    assert not any(k.startswith("vfo_") and k[-2:] == "_0" for k in changes0)
    _, changes1 = encode_full(full_settings(active_vfos=1))
    assert "vfo_freq_0" in changes1


def test_diverge_then_one_auto_push_then_resolve(tmp_path: Path) -> None:
    mirror, pd = seeded(tmp_path)
    good_crc = canonical_crc(mirror.current(ADDR))

    # A BEARING reports the accepted version but a wrong CRC: divergence.
    diverged = pd.observe_bearing(ADDR, mirror.version(ADDR), good_crc ^ 0xFFFF)
    assert diverged.state is ConfigState.DIVERGED
    assert diverged.auto_push is not None
    assert diverged.auto_push.kind == "full"
    assert pd.snapshot(ADDR).resynced  # sticky marker set (FR-7.7)

    # The auto full push is ACKed with the correct CRC: resolved.
    push = diverged.auto_push
    resolved = pd.on_ack(ADDR, push.version, push.expected_crc)
    assert resolved.state is ConfigState.IN_SYNC
    assert not pd.snapshot(ADDR).resynced


def test_diverge_again_after_auto_push_latches(tmp_path: Path) -> None:
    mirror, pd = seeded(tmp_path)
    good_crc = canonical_crc(mirror.current(ADDR))
    wrong = good_crc ^ 0xFFFF

    diverged = pd.observe_bearing(ADDR, mirror.version(ADDR), wrong)
    assert diverged.auto_push is not None

    # The auto push is ACKed but the CRC STILL mismatches: latch (FR-7.7).
    push = diverged.auto_push
    latched = pd.on_ack(ADDR, push.version, wrong)
    assert latched.state is ConfigState.LATCHED
    snap = pd.snapshot(ADDR)
    assert snap.latched
    assert snap.state is ConfigState.LATCHED


def test_kraken_down_ack_is_not_divergence(tmp_path: Path) -> None:
    from hornethunter_management.param_dist import STATUS_KRAKEN_DOWN

    _, pd = seeded(tmp_path)
    push = pd.prepare_delta(ADDR, {"uniform_gain": 7})
    assert push is not None
    result = pd.on_ack(ADDR, push.version, 0, status=STATUS_KRAKEN_DOWN)
    assert result.state is ConfigState.KRAKEN_DOWN
    assert not result.committed


def test_clear_latch_restores_in_sync(tmp_path: Path) -> None:
    mirror, pd = seeded(tmp_path)
    good_crc = canonical_crc(mirror.current(ADDR))
    wrong = good_crc ^ 0xFFFF
    diverged = pd.observe_bearing(ADDR, mirror.version(ADDR), wrong)
    pd.on_ack(ADDR, diverged.auto_push.version, wrong)  # type: ignore[union-attr]
    assert pd.snapshot(ADDR).latched
    pd.clear_latch(ADDR)
    assert not pd.snapshot(ADDR).latched
    assert pd.snapshot(ADDR).state is ConfigState.IN_SYNC
