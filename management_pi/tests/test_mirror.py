from pathlib import Path

from _settings import full_settings

from hornethunter_management.mirror import ConfigMirror


def test_seed_and_read(tmp_path: Path) -> None:
    m = ConfigMirror(tmp_path / "mirror.json")
    m.seed(1, full_settings(uniform_gain=3), version=0)
    assert m.has(1)
    assert m.current(1)["uniform_gain"] == 3
    assert m.version(1) == 0
    assert m.previous(1) is None


def test_persists_across_reload(tmp_path: Path) -> None:
    path = tmp_path / "mirror.json"
    m = ConfigMirror(path)
    m.seed(1, full_settings(uniform_gain=5), version=2)
    m.accept(1, full_settings(uniform_gain=7), version=3)

    reloaded = ConfigMirror(path)  # simulates a service restart (FR-7.9)
    assert reloaded.current(1)["uniform_gain"] == 7
    assert reloaded.version(1) == 3
    assert reloaded.previous(1)["uniform_gain"] == 5


def test_accept_demotes_current_to_previous(tmp_path: Path) -> None:
    m = ConfigMirror(tmp_path / "mirror.json")
    m.seed(1, full_settings(uniform_gain=1), version=0)
    m.accept(1, full_settings(uniform_gain=2), version=1)
    assert m.current(1)["uniform_gain"] == 2
    assert m.previous(1)["uniform_gain"] == 1


def test_revert_restores_previous(tmp_path: Path) -> None:
    m = ConfigMirror(tmp_path / "mirror.json")
    m.seed(1, full_settings(uniform_gain=1), version=0)
    m.accept(1, full_settings(uniform_gain=2), version=1)
    restored = m.revert(1)
    assert restored["uniform_gain"] == 1
    assert m.current(1)["uniform_gain"] == 1
    assert m.version(1) == 0


def test_revert_without_snapshot_is_a_noop(tmp_path: Path) -> None:
    m = ConfigMirror(tmp_path / "mirror.json")
    m.seed(1, full_settings(), version=0)
    assert m.revert(1) is None


def test_reverted_state_survives_reload(tmp_path: Path) -> None:
    path = tmp_path / "mirror.json"
    m = ConfigMirror(path)
    m.seed(1, full_settings(uniform_gain=1), version=0)
    m.accept(1, full_settings(uniform_gain=2), version=1)
    m.revert(1)
    assert ConfigMirror(path).current(1)["uniform_gain"] == 1
