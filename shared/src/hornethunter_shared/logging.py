"""Structured JSONL logging (FSD §20).

One JSON object per event, machine-parseable without regular expressions. Every
record carries both wall-clock and monotonic time (FR-20.7). Secrets present in the
KrakenSDR settings — notably `krakenpro_key` — are never written (NFR-22.4). Logging
never raises into the caller: on any write failure it drops the record and counts
the drop rather than stalling the poll cycle (NFR-20.1).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

REDACTED = "***"
_DEFAULT_SECRETS = frozenset({"krakenpro_key", "key", "psk", "passphrase", "link.key"})


class StructuredLogger:
    """Append-only JSONL logger with size-bounded rotation."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = 5_000_000,
        backup_count: int = 3,
        secret_keys: frozenset[str] = _DEFAULT_SECRETS,
    ) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.secret_keys = secret_keys
        self.dropped = 0
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, event: str, **fields: Any) -> None:
        """Write one event. Never raises; a failed write increments `dropped`."""
        record = {
            "event": event,
            "wall": time.time(),
            "mono": time.monotonic(),
            **self._redact(fields),
        }
        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        try:
            with self._lock:
                self._rotate_if_needed(len(line))
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
        except OSError:
            self.dropped += 1

    def _redact(self, fields: dict[str, Any]) -> dict[str, Any]:
        return {
            key: (REDACTED if key in self.secret_keys else value)
            for key, value in fields.items()
        }

    def _rotate_if_needed(self, incoming: int) -> None:
        if not self.path.exists():
            return
        if self.path.stat().st_size + incoming <= self.max_bytes:
            return
        for index in range(self.backup_count, 0, -1):
            src = self.path if index == 1 else self.path.with_suffix(f".{index - 1}")
            if not src.exists():
                continue
            src.replace(self.path.with_suffix(f".{index}"))
