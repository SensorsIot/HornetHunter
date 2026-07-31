"""Management Pi aggregator.

Collects BearingReports from every Kraken Pi and triangulates them into a fix.
The network service is not wired up yet; `--fix-from` triangulates reports from
a file so the geometry can be exercised without stations.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

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
    """Triangulate a batch of reports, ignoring stations that report no signal.

    A v2 seam: v1 displays bearings only and computes no fix (FSD 9.6). Reports
    without a position are skipped -- position rides only on change (FSD FR-9.5),
    so a caller must pair each report with the station's last known position
    before a fix can use it.
    """
    observations: list[tuple[LatLon, float]] = []
    for report in reports:
        if report.latitude is None or report.longitude is None:
            continue
        if report.confidence <= 0.0 or not report.has_data:
            continue
        observations.append((LatLon(report.latitude, report.longitude), report.bearing_deg))

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
    return run_server(config, listen, port)


def run_server(config: dict[str, Any], listen: Any, port: Any) -> int:
    """Start the master loop and the Flask UI (FSD §14).

    This is the real-run path: it is not unit-tested and its heavy imports are kept
    local so the tested code paths (`--fix-from`, `--version`) never load Flask. The
    UI binds to the operator-configured interface, defaulting to the local network
    (NFR-22.3).
    """
    import threading
    import time

    from hornethunter_management.master import Master, MasterConfig
    from hornethunter_management.mirror import ConfigMirror
    from hornethunter_management.ui import create_app
    from hornethunter_shared.carrier import SerialCarrier
    from hornethunter_shared.dtu import maybe_provision_dtu

    log_section = config.get("log", {})
    log_path = log_section.get("path", "/var/log/hornethunter/management.jsonl")
    mirror_section = config.get("mirror", {})
    mirror_path = mirror_section.get("path", "/var/lib/hornethunter/mirror.json")

    master_config = MasterConfig.from_toml(config)
    mirror = ConfigMirror(mirror_path)
    carrier_url = str(require(config, "carrier", "serial_url"))
    # The master DTU is the broadcast-monitor (0xFFFF, §19.2); a [dtu] address
    # override is honoured but should never be needed.
    dtu_cfg = config.get("dtu", {})
    master_addr = int(dtu_cfg.get("address", 0xFFFF)) if isinstance(dtu_cfg, dict) else 0xFFFF
    result = maybe_provision_dtu(config, carrier_url, address=master_addr)
    if result is not None:
        print(
            f"hornethunter-management: DTU provisioned entered={result.entered} "
            f"written={result.written or '{}'} mismatches={result.mismatches or '{}'}"
        )
    carrier = SerialCarrier(carrier_url)
    master = Master(carrier, master_config, mirror)

    def loop() -> None:
        while True:
            master.step(int(time.monotonic() * 1000))
            time.sleep(0.01)

    threading.Thread(target=loop, daemon=True).start()
    app = create_app(master, log_path=log_path)
    print(f"hornethunter-management: serving UI on {listen}:{port}")
    app.run(host=str(listen), port=int(port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
