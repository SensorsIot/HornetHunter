"""Kraken Pi KrakenProxy.

Reads direction-of-arrival estimates from the locally attached KrakenSDR and
publishes BearingReports to the Management Pi. The KrakenSDR link is not wired
up yet; `--self-test` exercises the plumbing without hardware.
"""

from __future__ import annotations

import argparse
from typing import Any

from hornethunter_kraken import __version__
from hornethunter_shared.config import load_config, require
from hornethunter_shared.messages import (
    FLAG_POSITION_PRESENT,
    BearingReport,
)

DEFAULT_CONFIG = "/etc/hornethunter/kraken.toml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hornethunter-kraken",
        description="Publish KrakenSDR direction-of-arrival reports to the Management Pi",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help=f"path to the station config (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="emit one synthetic report as JSON and exit; no KrakenSDR needed",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="run the KrakenProxy against the real serial carrier and KrakenSDR",
    )
    return parser


def synthetic_report(config: dict[str, object]) -> BearingReport:
    """Build a report from the configured station position, for --self-test.

    Carries no measurement: confidence is zero and the age is zero because
    nothing was measured. Reports have no absolute timestamp (FSD 9.5).
    """
    return BearingReport(
        station_id=str(require(config, "station", "id")),
        age_ms=0,
        bearing_deg=0.0,
        confidence=0.0,
        power_dbm=0.0,
        config_version=0,
        config_crc=0,
        flags=FLAG_POSITION_PRESENT,
        latitude=float(require(config, "station", "latitude")),
        longitude=float(require(config, "station", "longitude")),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)

    if args.self_test:
        print(synthetic_report(config).to_json())
        return 0

    if args.run:
        return _run_station(config)

    station = require(config, "station", "id")
    endpoint = require(config, "management", "endpoint")
    print(f"hornethunter-kraken: station {station} would publish to {endpoint}")
    print("KrakenSDR acquisition is not implemented yet")
    return 0


def _run_station(config: dict[str, object]) -> int:  # pragma: no cover - real hardware path
    """Construct the serial carrier and run the KrakenProxy loop (FSD §5).

    Imports the carrier and agent lazily so the host tier — and every other CLI
    path — never needs a serial device or a live KrakenSDR.
    """
    from hornethunter_shared.carrier import SerialCarrier

    from .agent import KrakenProxy
    from .doa_source import build_source
    from .settings_client import KrakenSettings, UrllibTransport
    from .supervisor import RecoveryPolicy, Supervisor, systemctl_restart

    def _get(section: str, key: str, default: Any) -> Any:
        value = config.get(section, {})
        return value.get(key, default) if isinstance(value, dict) else default

    carrier = SerialCarrier(str(_get("carrier", "serial_url", "/dev/serial0")))
    source = build_source(
        str(_get("kraken", "backend", "kraken")),
        doa_url=str(_get("kraken", "doa_url", "http://127.0.0.1:8081/DOA_value.html")),
        latitude=float(require(config, "station", "latitude")),
        longitude=float(require(config, "station", "longitude")),
    )
    settings_path = _get("kraken", "settings_path", None)
    settings = KrakenSettings(
        UrllibTransport(),
        settings_path=str(settings_path) if settings_path else None,
    )
    supervisor = None
    if _get("supervisor", "enabled", False):
        services = _get("supervisor", "services", ["krakensdr"])
        supervisor = Supervisor(
            policy=RecoveryPolicy(
                stall_after_s=float(_get("supervisor", "stall_after_s", 10.0)),
                backoff_s=float(_get("supervisor", "backoff_s", 30.0)),
                max_attempts=int(_get("supervisor", "max_attempts", 3)),
            ),
            recover=systemctl_restart(*services),
        )
    agent = KrakenProxy(
        carrier,
        config,
        source,
        settings,
        address=int(_get("link", "address", 1)),
        max_rate_hz=float(_get("stream", "max_rate_hz", 5.0)),
        supervisor=supervisor,
    )
    print(f"hornethunter-kraken: station {require(config, 'station', 'id')} running")
    agent.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
