"""Kraken Pi station agent.

Reads direction-of-arrival estimates from the locally attached KrakenSDR and
publishes BearingReports to the Management Pi. The KrakenSDR link is not wired
up yet; `--self-test` exercises the plumbing without hardware.
"""

from __future__ import annotations

import argparse
import time

from hornethunter_kraken import __version__
from hornethunter_shared.config import load_config, require
from hornethunter_shared.messages import BearingReport

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
    return parser


def synthetic_report(config: dict[str, object], now: float) -> BearingReport:
    """Build a report from the configured station position, for --self-test."""
    return BearingReport(
        station_id=str(require(config, "station", "id")),
        timestamp=now,
        latitude=float(require(config, "station", "latitude")),
        longitude=float(require(config, "station", "longitude")),
        bearing_deg=0.0,
        confidence=0.0,
        frequency_hz=int(require(config, "radio", "frequency_hz")),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)

    if args.self_test:
        print(synthetic_report(config, time.time()).to_json())
        return 0

    station = require(config, "station", "id")
    endpoint = require(config, "management", "endpoint")
    print(f"hornethunter-kraken: station {station} would publish to {endpoint}")
    print("KrakenSDR acquisition is not implemented yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
