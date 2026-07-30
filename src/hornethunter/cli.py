"""Command line entry point."""

import argparse

from hornethunter import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hornethunter",
        description="Radio direction finding tools for tracking invasive hornets",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    print("hornethunter: nothing to do yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
