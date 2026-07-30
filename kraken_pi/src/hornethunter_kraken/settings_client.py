"""Kraken settings interface (FSD §13).

Reads and writes the KrakenSDR configuration on the local host. The interface is
**route-agnostic** (FR-13.5): one internal read/write contract with two write-route
implementations — `POST :8042/settings` (JSON) and `POST :8081/upload?path=/`
(multipart) — probed at startup to discover which the local station provides. When
neither is available the station is reported **read-only** (FR-13.6) and still
serves reads for divergence detection (§7.5).

`apply_delta` reads current settings, merges the changed fields, writes the merged
result, **re-reads**, and computes the canonical CRC from that read-back (FR-13.1,
FR-13.2, FR-7.5); it reports every field the KrakenSDR altered or clamped relative
to the request (FR-13.3). The HTTP layer sits behind a small transport interface so
host tests run against an in-memory `FakeTransport` with no network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from hornethunter_shared.registry import BY_KEY, canonical_crc

# ACK status bits reported when a push cannot be applied (§13.5, §10.3).
STATUS_OK = 0
STATUS_READ_ONLY = 1 << 0
STATUS_KRAKEN_DOWN = 1 << 1

RouteKind = Literal["json", "multipart"]


class HttpTransport(Protocol):
    """Primitive HTTP operations. Implementations raise `OSError` on a network
    fault and otherwise return the HTTP status code for writes."""

    def get(self, url: str) -> bytes: ...

    def post_json(self, url: str, body: bytes) -> int: ...

    def post_multipart(self, url: str, field_name: str, body: bytes) -> int: ...


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of a parameter application (§13.2, §13.3)."""

    applied: bool
    read_only: bool
    config_version: int
    config_crc: int
    status: int = STATUS_OK
    reason: str | None = None
    altered: dict[str, Any] = field(default_factory=dict)


class UrllibTransport:
    """Real HTTP transport over `urllib.request` (bench/field only)."""

    def __init__(self, *, timeout_s: float = 5.0) -> None:
        self._timeout = timeout_s

    def get(self, url: str) -> bytes:
        with urllib.request.urlopen(url, timeout=self._timeout) as response:
            data: bytes = response.read()
            return data

    def post_json(self, url: str, body: bytes) -> int:
        request = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        return self._status(request)

    def post_multipart(self, url: str, field_name: str, body: bytes) -> int:
        boundary = "----hornethunter-boundary"
        payload = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; '
            f'filename="{field_name}"\r\n'
            "Content-Type: application/json\r\n\r\n"
        ).encode() + body + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        return self._status(request)

    def _status(self, request: urllib.request.Request) -> int:
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                status: int = response.status
                return status
        except urllib.error.HTTPError as exc:
            return int(exc.code)


class KrakenSettings:
    """Route-agnostic settings client (§13)."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        read_url: str = "http://127.0.0.1:8081/settings.json",
        json_url: str = "http://127.0.0.1:8042/settings",
        multipart_url: str = "http://127.0.0.1:8081/upload?path=/",
        multipart_field: str = "settings.json",
        logger: Any | None = None,
    ) -> None:
        self._t = transport
        self._read_url = read_url
        self._json_url = json_url
        self._multipart_url = multipart_url
        self._multipart_field = multipart_field
        self._logger = logger
        self._settings: dict[str, Any] = {}
        self._version = 0
        self._crc = 0
        self._route: RouteKind | None = None
        self._route_url = ""
        self._probe()

    # -- state ---------------------------------------------------------------

    @property
    def read_only(self) -> bool:
        return self._route is None

    @property
    def config_version(self) -> int:
        return self._version

    @property
    def config_crc(self) -> int:
        return self._crc

    # -- reads ---------------------------------------------------------------

    def read(self) -> dict[str, Any]:
        """Read current settings (`GET settings.json`) and refresh the cached CRC."""
        self._settings = self._read_raw()
        self._recompute_crc()
        return dict(self._settings)

    def _read_raw(self) -> dict[str, Any]:
        raw = self._t.get(self._read_url)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("settings.json is not a JSON object")
        return dict(data)

    def _recompute_crc(self) -> None:
        try:
            self._crc = canonical_crc(self._settings)
        except KeyError:
            # An incomplete read cannot yield a canonical CRC; leave the last value.
            self._log("settings_crc_incomplete")

    # -- write-route discovery (FR-13.5) -------------------------------------

    def _probe(self) -> None:
        try:
            self._settings = self._read_raw()
        except OSError:
            self._log("settings_unreachable_at_startup")
            return
        self._recompute_crc()
        body = _json_bytes(self._settings)
        candidates: tuple[tuple[RouteKind, str], ...] = (
            ("json", self._json_url),
            ("multipart", self._multipart_url),
        )
        for kind, url in candidates:
            try:
                status = self._post(kind, url, body)
            except OSError:
                continue
            if status == 200:
                self._route = kind
                self._route_url = url
                self._log("settings_write_route", route=kind, url=url)
                return
        self._log("settings_read_only")

    def _post(self, kind: RouteKind, url: str, body: bytes) -> int:
        if kind == "json":
            return self._t.post_json(url, body)
        return self._t.post_multipart(url, self._multipart_field, body)

    # -- writes (FR-13.1, FR-13.2, FR-13.3) ----------------------------------

    def apply_delta(self, changes: dict[str, Any]) -> ApplyResult:
        """Merge `changes` into current settings, write, re-read, and report."""
        if self._route is None:
            self._log("settings_push_rejected", reason="read_only")
            return ApplyResult(
                applied=False,
                read_only=True,
                config_version=self._version,
                config_crc=self._crc,
                status=STATUS_READ_ONLY,
                reason="read_only",
            )

        try:
            current = self._read_raw()
            merged = {**current, **changes}
            status = self._post(self._route, self._route_url, _json_bytes(merged))
            if status != 200:
                raise OSError(f"write route returned {status}")
            readback = self._read_raw()
        except OSError as exc:
            self._log("settings_kraken_down", error=str(exc))
            return ApplyResult(
                applied=False,
                read_only=False,
                config_version=self._version,
                config_crc=self._crc,
                status=STATUS_KRAKEN_DOWN,
                reason="kraken_down",
            )

        self._settings = readback
        self._recompute_crc()
        self._version = (self._version + 1) & 0xFF
        altered = {
            key: readback.get(key)
            for key, requested in changes.items()
            if key in BY_KEY and readback.get(key) != requested
        }
        self._log(
            "settings_applied",
            fields=sorted(changes),
            config_version=self._version,
            config_crc=self._crc,
            altered=sorted(altered),
        )
        return ApplyResult(
            applied=True,
            read_only=False,
            config_version=self._version,
            config_crc=self._crc,
            reason=None,
            altered=altered,
        )

    def _log(self, event: str, **fields: Any) -> None:
        if self._logger is not None:
            self._logger.event(event, **fields)


def _json_bytes(settings: dict[str, Any]) -> bytes:
    return json.dumps(settings, separators=(",", ":"), sort_keys=True).encode("utf-8")
