"""Management Pi aggregator.

Collects BearingReports from every Kraken Pi and triangulates them into a fix.
The network service is not wired up yet; `--fix-from` triangulates reports from
a file so the geometry can be exercised without stations.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hornethunter_management import __version__
from hornethunter_shared.config import load_config, require
from hornethunter_shared.geo import LatLon, triangulate
from hornethunter_shared.messages import BearingReport

DEFAULT_CONFIG = "/etc/hornethunter/management.toml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hornethunter-management",
        description="Aggregate station bearings into transmitter fixes",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help=f"path to the management config (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--fix-from",
        metavar="FILE",
        help="triangulate newline-delimited BearingReport JSON and exit",
    )
    return parser


def fix_from_reports(reports: list[BearingReport]) -> LatLon | None:
    """Triangulate a batch of reports, ignoring stations that report no signal."""
    observations = [
        (LatLon(r.latitude, r.longitude), r.bearing_deg) for r in reports if r.confidence > 0.0
    ]
    if len(observations) < 2:
        return None
    return triangulate(observations)


def read_reports(path: str | Path) -> list[BearingReport]:
    lines = Path(path).read_text().splitlines()
    return [BearingReport.from_json(line) for line in lines if line.strip()]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.fix_from:
        fix = fix_from_reports(read_reports(args.fix_from))
        if fix is None:
            print("no fix: fewer than two usable bearings, or they do not cross", file=sys.stderr)
            return 1
        print(f"{fix.lat:.6f},{fix.lon:.6f}")
        return 0

    config = load_config(args.config)
    listen = require(config, "server", "listen")
    port = require(config, "server", "port")
    print(f"hornethunter-management: would serve on {listen}:{port}")
    print("report intake and map UI are not implemented yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
