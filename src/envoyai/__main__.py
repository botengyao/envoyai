"""Command-line interface for envoyai.

Subcommands:

* ``envoyai download-aigw`` — pre-fetch the pinned ``aigw`` binary into the
  envoyai cache. Useful for CI, Dockerfile ``RUN`` steps, and air-gapped
  installs where you want the download out of the way before any call.
* ``envoyai where`` — print the path envoyai would use for ``aigw``.
* ``envoyai version`` — print the installed envoyai version and the pinned
  ``aigw`` version.
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="envoyai",
        description="Python SDK for Envoy AI Gateway.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_dl = sub.add_parser("download-aigw", help="Fetch the pinned aigw binary")
    p_dl.add_argument(
        "--version",
        default=None,
        help="Override the aigw version (default: the one envoyai was built against)",
    )
    p_dl.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress messages on stderr",
    )

    sub.add_parser(
        "where",
        help="Print the resolved aigw binary path (no download)",
    )

    sub.add_parser("version", help="Print envoyai + aigw versions")

    args = parser.parse_args(argv)

    if args.command == "download-aigw":
        from envoyai._internal.aigw_bootstrap import AIGW_VERSION, ensure_downloaded

        version = args.version or AIGW_VERSION
        path = ensure_downloaded(version=version, verbose=not args.quiet)
        print(path)
        return 0

    if args.command == "where":
        from envoyai._internal.aigw_process import find_aigw

        try:
            path = find_aigw(auto_download=False, verbose=False)
        except Exception as exc:
            print(f"envoyai: {exc}", file=sys.stderr)
            return 1
        print(path)
        return 0

    if args.command == "version":
        from envoyai import __version__
        from envoyai._internal.aigw_bootstrap import AIGW_VERSION

        print(f"envoyai {__version__}")
        print(f"aigw    {AIGW_VERSION} (pinned)")
        return 0

    return 0  # pragma: no cover — argparse enforces a subcommand


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
