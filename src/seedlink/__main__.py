"""Package entry point for the SeedLink desktop application."""

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
    parser = build_parser()
    parser.parse_args(argv)
    from seedlink.desktop import run_desktop

    return run_desktop(argv)


if __name__ == "__main__":
    raise SystemExit(main())
