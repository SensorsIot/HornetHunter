"""Kraken Pi station agent.

Reads direction-of-arrival estimates from the locally attached KrakenSDR and
publishes BearingReports to the Management Pi. The KrakenSDR link is not wired
up yet; `--self-test` exercises the plumbing without hardware.
"""

from __future__ import annotations

import argparse

from hornethunter_kraken import __version__
from hornethunter_shared.config import load_config, require
from hornethunter_shared.messages import FLAG_POSITION_PRESENT, BearingReport

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

    station = require(config, "station", "id")
    endpoint = require(config, "management", "endpoint")
    print(f"hornethunter-kraken: station {station} would publish to {endpoint}")
    print("KrakenSDR acquisition is not implemented yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
