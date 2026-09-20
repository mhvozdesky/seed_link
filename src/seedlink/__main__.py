"""Minimal package entry point used before the desktop shell is implemented."""

from __future__ import annotations

import argparse

from seedlink import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seedlink",
        description="SeedLink — локальний звіт Seed Selector",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"SeedLink {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
