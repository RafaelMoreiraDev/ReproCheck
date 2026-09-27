"""Command line interface for ReproCheck (argparse, stdlib only).

Exit codes
----------

``0``
    The command completed and the verdict is ``PASS`` (or the command was a
    ``scan``, which never attempts a reproduction).
``1``
    The verdict is ``PARTIAL``: the reproduction succeeded as far as it went,
    but something important is unverified or a warning is open.
``2``
    The verdict is ``FAIL``: a failure of the reproduction itself was observed.
``3``
    Operational error: the path is unusable or a report could not be written.

``2`` is also what argparse itself uses for a malformed command line, which is
why operational errors use ``3`` instead.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reprocheck import __version__
from reprocheck.models import ScanReport, VerdictStatus
from reprocheck.reporters import (
    DEFAULT_MARKDOWN_NAME,
    DEFAULT_REPORT_NAME,
    format_report,
    write_markdown,
    write_report,
)
from reprocheck.reproduction.runner import ReproductionError, reproduce
from reprocheck.scanner import ScanError, scan

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_FAIL = 2
EXIT_OPERATIONAL = 3

#: Kept for callers that used the old name; it now means an operational error.
EXIT_ERROR = EXIT_OPERATIONAL
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    """Build the ``reprocheck`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="reprocheck",
        description=(
            "Read-only reproducibility scanner for Python projects. "
            "ReproCheck inspects a directory and writes a JSON report plus a "
            "Markdown report; it never modifies the scanned project."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"reprocheck {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser(
        "scan", help="analyse a project directory and write both reports"
    )
    _add_common_arguments(scan_parser)
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
    _add_common_arguments(reproduce_parser)
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


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", metavar="<path>", help="path of the project to analyse")
    parser.add_argument(
        "--json",
        metavar="<arquivo>",
        default=None,
        help=f"JSON report destination (default: ./{DEFAULT_REPORT_NAME})",
    )
    parser.add_argument(
        "--markdown",
        metavar="<arquivo>",
        default=None,
        help=f"Markdown report destination (default: ./{DEFAULT_MARKDOWN_NAME})",
    )


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
        return EXIT_OPERATIONAL

    try:
        output, markdown = _write_reports(report, args)
    except OSError as exc:
        print(f"reprocheck: error: could not write report: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    print(
        format_report(report, str(output), verbose=args.verbose, markdown=str(markdown))
    )
    return EXIT_OK


def _run_reproduce(args: argparse.Namespace) -> int:
    try:
        report = reproduce(
            args.path,
            network=args.network,
            keep_workspace=args.keep_workspace,
            runtime_checks=args.runtime_checks,
        )
    except (ScanError, ReproductionError) as exc:
        print(f"reprocheck: error: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    try:
        output, markdown = _write_reports(report, args)
    except OSError as exc:
        print(f"reprocheck: error: could not write report: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    print(
        format_report(report, str(output), verbose=args.verbose, markdown=str(markdown))
    )
    return _exit_code(report)


def _write_reports(report: ScanReport, args: argparse.Namespace) -> tuple[Path, Path]:
    """Write the JSON report (primary) and the Markdown report."""
    json_destination = (
        Path(args.json) if args.json else Path.cwd() / DEFAULT_REPORT_NAME
    )
    markdown_destination = (
        Path(args.markdown) if args.markdown else Path.cwd() / DEFAULT_MARKDOWN_NAME
    )
    output = write_report(report, json_destination)
    markdown = write_markdown(report, markdown_destination)
    return output, markdown


def _exit_code(report: ScanReport) -> int:
    if report.verdict.status is VerdictStatus.FAIL:
        return EXIT_FAIL
    if report.verdict.status is VerdictStatus.PARTIAL:
        return EXIT_PARTIAL
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
