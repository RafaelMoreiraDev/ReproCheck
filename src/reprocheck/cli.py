"""Command line interface for ReproCheck (argparse, stdlib only)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reprocheck import __version__
from reprocheck.models import Finding, Severity
from reprocheck.reporters import DEFAULT_REPORT_NAME, format_report, write_report
from reprocheck.reproduction.runner import ReproductionError, reproduce
from reprocheck.scanner import ScanError, scan

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    """Build the ``reprocheck`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="reprocheck",
        description=(
            "Read-only reproducibility scanner for Python projects. "
            "ReproCheck inspects a directory and writes a JSON report; it never "
            "modifies the scanned project."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"reprocheck {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser(
        "scan", help="analyse a project directory and write a JSON report"
    )
    scan_parser.add_argument(
        "path", metavar="<path>", help="path of the project to scan"
    )
    scan_parser.add_argument(
        "--json",
        metavar="<arquivo>",
        default=None,
        help=f"report destination (default: ./{DEFAULT_REPORT_NAME})",
    )
    scan_parser.add_argument(
        "--verbose",
        action="store_true",
        help="print extra details (HEAD, package managers, README commands)",
    )

    reproduce_parser = subparsers.add_parser(
        "reproduce",
        help=(
            "attempt a controlled reproduction in an isolated temporary "
            "workspace; the analysed project is never modified"
        ),
    )
    reproduce_parser.add_argument(
        "path", metavar="<path>", help="path of the project to reproduce"
    )
    reproduce_parser.add_argument(
        "--json",
        metavar="<arquivo>",
        default=None,
        help=f"report destination (default: ./{DEFAULT_REPORT_NAME})",
    )
    reproduce_parser.add_argument(
        "--network",
        action="store_true",
        help="allow the installation step to reach a package index (off by default)",
    )
    reproduce_parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help="keep the temporary workspace even after a successful attempt",
    )
    reproduce_parser.add_argument(
        "--runtime-checks",
        action="store_true",
        help=(
            "also import the installed modules and collect tests; this executes "
            "project code, so it is off by default"
        ),
    )
    reproduce_parser.add_argument(
        "--verbose",
        action="store_true",
        help="print the reproduction steps and log locations",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``reprocheck`` console script."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "reproduce":
        return _run_reproduce(args)
    if args.command != "scan":  # pragma: no cover - argparse enforces this
        parser.print_help()
        return EXIT_USAGE

    try:
        report = scan(args.path)
    except ScanError as exc:
        print(f"reprocheck: error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    destination = Path(args.json) if args.json else Path.cwd() / DEFAULT_REPORT_NAME
    try:
        output = write_report(report, destination)
    except OSError as exc:
        print(f"reprocheck: error: could not write report: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(format_report(report, str(output), verbose=args.verbose))
    return EXIT_OK


def _run_reproduce(args: argparse.Namespace) -> int:
    try:
        report = reproduce(
            args.path, network=args.network, keep_workspace=args.keep_workspace
        )
    except (ScanError, ReproductionError) as exc:
        print(f"reprocheck: error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    destination = Path(args.json) if args.json else Path.cwd() / DEFAULT_REPORT_NAME
    try:
        output = write_report(report, destination)
    except OSError as exc:
        print(f"reprocheck: error: could not write report: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(format_report(report, str(output), verbose=args.verbose))
    return EXIT_ERROR if _has_error(report.reproduction_findings) else EXIT_OK


def _has_error(findings: list[Finding]) -> bool:
    return any(finding.severity is Severity.ERROR for finding in findings)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
