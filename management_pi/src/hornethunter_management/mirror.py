"""Per-station configuration mirror (FSD §7.6, §7.9).

The Management Pi persists, for each station, the current accepted settings, its
`config_version`, and the previous known-good snapshot, to a JSON file that
survives restarts (FR-7.9, AT-14). Reverting swaps the current settings back to the
previous snapshot (§7.6) so a change that breaks the KrakenSDR can always be undone
— the station agent's command channel is independent of DSP health (§2.3).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StationMirror:
    """The persisted state for one station."""

    settings: dict[str, Any]
    config_version: int
    previous: dict[str, Any] | None = None
    previous_version: int | None = None


class ConfigMirror:
    """A JSON-backed store of `StationMirror` records keyed by HH-Link address.

    Every mutation is written through to disk with an atomic replace, so a crash
    mid-write never leaves a truncated mirror.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._stations: dict[str, StationMirror] = {}
        if self.path.is_file():
            self._load()

    def _load(self) -> None:
        raw: Any = json.loads(self.path.read_text(encoding="utf-8"))
        for addr, entry in raw.items():
            self._stations[str(addr)] = StationMirror(
                settings=entry["settings"],
                config_version=entry["config_version"],
                previous=entry.get("previous"),
                previous_version=entry.get("previous_version"),
            )

    def _save(self) -> None:
        payload = {
            addr: {
                "settings": m.settings,
                "config_version": m.config_version,
                "previous": m.previous,
                "previous_version": m.previous_version,
            }
            for addr, m in self._stations.items()
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def has(self, addr: int) -> bool:
        return str(addr) in self._stations

    def get(self, addr: int) -> StationMirror | None:
        return self._stations.get(str(addr))

    def current(self, addr: int) -> dict[str, Any] | None:
        m = self._stations.get(str(addr))
        return dict(m.settings) if m is not None else None

    def version(self, addr: int) -> int:
        m = self._stations.get(str(addr))
        return m.config_version if m is not None else 0

    def previous(self, addr: int) -> dict[str, Any] | None:
        m = self._stations.get(str(addr))
        if m is None or m.previous is None:
            return None
        return dict(m.previous)

    def seed(self, addr: int, settings: dict[str, Any], version: int = 0) -> None:
        """Establish the first baseline for a station from a full-set read (§7.7).

        No previous snapshot exists yet, so a revert before any change is a no-op.
        """
        self._stations[str(addr)] = StationMirror(
            settings=dict(settings), config_version=version, previous=None, previous_version=None
        )
        self._save()

    def accept(self, addr: int, settings: dict[str, Any], version: int) -> None:
        """Accept a new configuration, demoting the current one to known-good.

        The prior accepted settings become the previous snapshot, so a subsequent
        revert restores exactly the configuration in force before this change.
        """
        existing = self._stations.get(str(addr))
        previous = existing.settings if existing is not None else None
        previous_version = existing.config_version if existing is not None else None
        self._stations[str(addr)] = StationMirror(
            settings=dict(settings),
            config_version=version,
            previous=previous,
            previous_version=previous_version,
        )
        self._save()

    def revert(self, addr: int) -> dict[str, Any] | None:
        """Restore the previous known-good snapshot (FR-14.7). Returns the restored
        settings, or None when there is no snapshot to revert to."""
        existing = self._stations.get(str(addr))
        if existing is None or existing.previous is None:
            return None
        restored = existing.previous
        restored_version = (
            existing.previous_version
            if existing.previous_version is not None
            else existing.config_version
        )
        self._stations[str(addr)] = StationMirror(
            settings=dict(restored),
            config_version=restored_version,
            previous=None,
            previous_version=None,
        )
        self._save()
        return dict(restored)
