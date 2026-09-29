"""Command line interface for ReproCheck (argparse, stdlib only).

Exit codes
----------

``0``
    The command completed. For ``reproduce`` the verdict is ``PASS``; for
    ``baseline compare`` the comparison completed, **whether or not anything
    changed** — a change is a fact, not an error.
``1``
    ``reproduce`` only: the verdict is ``PARTIAL``.
``2``
    ``reproduce`` only: the verdict is ``FAIL``.
``3``
    Operational error: the path is unusable, a report could not be written, or a
    baseline was missing, invalid or written in an unknown schema.

``2`` is also what argparse itself uses for a malformed command line, which is
why operational errors use ``3``.

Where files are written
-----------------------

With no destination given, reports go to a per-user state directory
(``REPROCHECK_STATE_DIR``, else ``%LOCALAPPDATA%\\reprocheck`` on Windows, else
``$XDG_STATE_HOME/reprocheck`` or ``~/.local/state/reprocheck``), in a
sub-directory named after the analysed project. Nothing is ever written inside
the analysed project unless the user asks for it with ``--json``,
``--markdown``, ``--output-dir`` or ``--output``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reprocheck import package_version
from reprocheck.diff import (
    BaselineError,
    compare_reports,
    ensure_same_project,
    load_baseline,
    load_report,
    save_baseline,
)
from reprocheck.fix import run_fix, run_rollback
from reprocheck.fix.models import ApplicationStatus, FixApplicationResult
from reprocheck.models import ScanReport, VerdictStatus
from reprocheck.output import (
    BASELINE_NAME,
    DIFF_MARKDOWN_NAME,
    DIFF_NAME,
    FIX_MARKDOWN_NAME,
    FIX_NAME,
    REPORT_MARKDOWN_NAME,
    REPORT_NAME,
    SUGGESTIONS_MARKDOWN_NAME,
    SUGGESTIONS_NAME,
    resolve_destination,
)
from reprocheck.reporters import format_report, write_markdown, write_report
from reprocheck.reporters.diff import write_diff_json, write_diff_markdown
from reprocheck.reporters.fix import write_fix_json, write_fix_markdown
from reprocheck.reporters.suggestions import (
    write_suggestions_json,
    write_suggestions_markdown,
)
from reprocheck.reporters.terminal import format_diff, format_fix, format_suggestions
from reprocheck.reproduction.runner import ReproductionError, reproduce
from reprocheck.scanner import (
    ScanError,
    build_report,
    collect_facts,
    resolve_target,
    scan,
)
from reprocheck.suggest import suggest

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_FAIL = 2
EXIT_OPERATIONAL = 3
EXIT_STALE = 5
EXIT_ROLLED_BACK = 6
EXIT_ROLLBACK_FAILED = 7

#: Kept for callers that used the old name; it now means an operational error.
EXIT_ERROR = EXIT_OPERATIONAL
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    """Build the ``reprocheck`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="reprocheck",
        description=(
            "Read-only reproducibility scanner for Python projects. "
            "ReproCheck inspects a directory and writes a JSON report, a Markdown "
            "report and, on request, a comparison against a saved baseline; it "
            "never modifies the scanned project."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"reprocheck {package_version()}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser(
        "scan", help="analyse a project directory and write both reports"
    )
    _add_report_arguments(scan_parser)
    scan_parser.add_argument(
        "path", metavar="<path>", help="path of the project to scan"
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
    _add_report_arguments(reproduce_parser)
    reproduce_parser.add_argument(
        "path", metavar="<path>", help="path of the project to reproduce"
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
        "--conda",
        action="store_true",
        help=(
            "reproduce the Conda environment instead of using pip; off by "
            "default, because a Conda environment is created by running a "
            "third-party package manager that downloads and executes packages"
        ),
    )
    reproduce_parser.add_argument(
        "--conda-env",
        metavar="<file>",
        default=None,
        help=(
            "which environment file to reproduce with --conda; required when "
            "the project declares more than one"
        ),
    )
    reproduce_parser.add_argument(
        "--conda-manager",
        metavar="<name>",
        default=None,
        help=(
            "use this Conda-compatible manager instead of discovering one "
            "(conda, mamba or micromamba)"
        ),
    )
    reproduce_parser.add_argument(
        "--verbose",
        action="store_true",
        help="print the reproduction steps and log locations",
    )

    _add_baseline_parsers(subparsers)
    _add_suggest_parser(subparsers)
    _add_fix_parser(subparsers)
    return parser


def _add_report_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        metavar="<arquivo>",
        default=None,
        help=f"JSON report destination (default: the state directory, {REPORT_NAME})",
    )
    parser.add_argument(
        "--markdown",
        metavar="<arquivo>",
        default=None,
        help=(
            "Markdown report destination (default: the state directory, "
            f"{REPORT_MARKDOWN_NAME})"
        ),
    )
    parser.add_argument(
        "--output-dir",
        metavar="<diretorio>",
        default=None,
        help=(
            "directory for both reports when --json/--markdown are not given; "
            "explicit paths always win"
        ),
    )


def _add_baseline_parsers(subparsers) -> None:
    baseline = subparsers.add_parser(
        "baseline", help="save a report as a reference, or compare against one"
    )
    baseline_actions = baseline.add_subparsers(dest="action", required=True)

    save = baseline_actions.add_parser(
        "save", help="save a report as a baseline for later comparisons"
    )
    save.add_argument(
        "report", metavar="<report.json>", help="JSON report to use as baseline"
    )
    save.add_argument(
        "--output",
        metavar="<arquivo>",
        default=None,
        help=f"baseline destination (default: the state directory, {BASELINE_NAME})",
    )
    save.add_argument(
        "--force",
        action="store_true",
        help="replace an existing baseline instead of refusing",
    )

    compare = baseline_actions.add_parser(
        "compare", help="compare a report against a saved baseline"
    )
    compare.add_argument(
        "baseline", metavar="<baseline.json>", help="baseline to compare against"
    )
    compare.add_argument("report", metavar="<report.json>", help="report to compare")
    compare.add_argument(
        "--json",
        metavar="<arquivo>",
        default=None,
        help=f"diff destination (default: the state directory, {DIFF_NAME})",
    )
    compare.add_argument(
        "--markdown",
        metavar="<arquivo>",
        default=None,
        help=f"Markdown diff destination (default: {DIFF_MARKDOWN_NAME})",
    )
    compare.add_argument(
        "--output-dir",
        metavar="<diretorio>",
        default=None,
        help="directory for both diff files when --json/--markdown are not given",
    )


def _add_suggest_parser(subparsers) -> None:
    suggest = subparsers.add_parser(
        "suggest",
        help=(
            "propose deterministic fixes for the findings of a static scan; "
            "never modifies the analysed project"
        ),
    )
    suggest.add_argument(
        "path", metavar="<path>", help="path of the project to suggest for"
    )
    suggest.add_argument(
        "--json",
        metavar="<arquivo>",
        default=None,
        help=(
            f"suggestion destination (default: the state directory, {SUGGESTIONS_NAME})"
        ),
    )
    suggest.add_argument(
        "--markdown",
        metavar="<arquivo>",
        default=None,
        help=(
            f"Markdown suggestion destination (default: {SUGGESTIONS_MARKDOWN_NAME})"
        ),
    )
    suggest.add_argument(
        "--output-dir",
        metavar="<diretorio>",
        default=None,
        help="directory for both files when --json/--markdown are not given",
    )


def _add_fix_parser(subparsers) -> None:
    fix = subparsers.add_parser(
        "fix",
        help=("apply exactly one SAFE suggestion; a dry run unless --apply is given"),
    )
    fix.add_argument("path", metavar="<path>", help="path of the project to fix")
    target = fix.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--suggestion",
        metavar="<ID>",
        default=None,
        help="identifier of the suggestion to show or apply, e.g. FIX-RC140-001",
    )
    target.add_argument(
        "--rollback",
        metavar="<record-id>",
        default=None,
        help="identifier of an application record whose change is to be undone",
    )
    fix.add_argument(
        "--apply",
        action="store_true",
        help="actually write the change; without it nothing is modified",
    )
    fix.add_argument(
        "--json",
        metavar="<arquivo>",
        default=None,
        help=f"fix report destination (default: the state directory, {FIX_NAME})",
    )
    fix.add_argument(
        "--markdown",
        metavar="<arquivo>",
        default=None,
        help=f"Markdown fix destination (default: {FIX_MARKDOWN_NAME})",
    )
    fix.add_argument(
        "--output-dir",
        metavar="<diretorio>",
        default=None,
        help="directory for both files when --json/--markdown are not given",
    )


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``reprocheck`` console script."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "reproduce":
        return _run_reproduce(args)
    if args.command == "baseline":
        if args.action == "save":
            return _run_baseline_save(args)
        return _run_baseline_compare(args)
    if args.command == "suggest":
        return _run_suggest(args)
    if args.command == "fix":
        return _run_fix(args)
    if args.command != "scan":  # pragma: no cover - argparse enforces this
        parser.print_help()
        return EXIT_USAGE

    try:
        report = scan(args.path)
    except ScanError as exc:
        print(f"reprocheck: error: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    try:
        output, markdown = _write_reports(report, args, args.path)
    except OSError as exc:
        print(f"reprocheck: error: could not write report: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    print(
        format_report(report, str(output), verbose=args.verbose, markdown=str(markdown))
    )
    return EXIT_OK


def _run_reproduce(args: argparse.Namespace) -> int:
    if getattr(args, "conda_env", None) and not getattr(args, "conda", False):
        print(
            "reprocheck: error: --conda-env has no effect without --conda",
            file=sys.stderr,
        )
        return EXIT_OPERATIONAL
    if getattr(args, "conda_manager", None) and not getattr(args, "conda", False):
        print(
            "reprocheck: error: --conda-manager has no effect without --conda",
            file=sys.stderr,
        )
        return EXIT_OPERATIONAL
    try:
        report = reproduce(
            args.path,
            network=args.network,
            keep_workspace=args.keep_workspace,
            runtime_checks=args.runtime_checks,
            conda=getattr(args, "conda", False),
            conda_env=getattr(args, "conda_env", None),
            conda_manager=getattr(args, "conda_manager", None),
        )
    except (ScanError, ReproductionError) as exc:
        print(f"reprocheck: error: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    try:
        output, markdown = _write_reports(report, args, args.path)
    except OSError as exc:
        print(f"reprocheck: error: could not write report: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    print(
        format_report(report, str(output), verbose=args.verbose, markdown=str(markdown))
    )
    return _exit_code(report)


def _run_baseline_save(args: argparse.Namespace) -> int:
    try:
        report = load_report(args.report)
        destination = (
            Path(args.output).expanduser()
            if args.output
            else _default_baseline_path(report)
        )
        written = save_baseline(
            report, destination, force=args.force, source=args.report
        )
    except BaselineError as exc:
        print(f"reprocheck: error: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL
    except OSError as exc:
        print(f"reprocheck: error: could not write baseline: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    identity = load_baseline(written).identity
    print("ReproCheck baseline saved")
    print("")
    print(f"  project: {identity.name}")
    if identity.path:
        print(f"  path: {identity.path}")
    print(f"  report schema: {identity.report_schema_version}")
    print(f"  baseline: {written}")
    return EXIT_OK


def _run_baseline_compare(args: argparse.Namespace) -> int:
    try:
        baseline = load_baseline(args.baseline)
        current = load_report(args.report)
        ensure_same_project(baseline.report, current)
        diff = compare_reports(baseline.report, current)
        project = (current.get("project") or {}).get("path")
        json_destination = resolve_destination(
            args.json, DIFF_NAME, args.output_dir, project
        )
        markdown_destination = resolve_destination(
            args.markdown, DIFF_MARKDOWN_NAME, args.output_dir, project
        )
        json_path = write_diff_json(diff, json_destination)
        markdown_path = write_diff_markdown(diff, markdown_destination)
    except BaselineError as exc:
        print(f"reprocheck: error: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL
    except OSError as exc:
        print(
            f"reprocheck: error: could not write the comparison: {exc}", file=sys.stderr
        )
        return EXIT_OPERATIONAL

    absent = baseline.absent_sections()
    print(
        format_diff(
            diff,
            str(json_path),
            str(markdown_path),
            baseline=str(args.baseline),
            current=str(args.report),
        )
    )
    if absent:
        print("")
        print(
            "  note: the baseline predates these report sections, which were not "
            f"compared: {', '.join(absent)}"
        )
    return EXIT_OK


def _run_suggest(args: argparse.Namespace) -> int:
    """Propose fixes. Read-only: no file of the project is ever written."""
    try:
        facts = collect_facts(resolve_target(args.path))
        report = build_report(facts)
        proposals = suggest(facts, report)
    except ScanError as exc:
        print(f"reprocheck: error: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    try:
        json_path = write_suggestions_json(
            proposals,
            resolve_destination(
                args.json, SUGGESTIONS_NAME, args.output_dir, report.project.path
            ),
        )
        markdown_path = write_suggestions_markdown(
            proposals,
            resolve_destination(
                args.markdown,
                SUGGESTIONS_MARKDOWN_NAME,
                args.output_dir,
                report.project.path,
            ),
        )
    except OSError as exc:
        print(f"reprocheck: error: could not write suggestions: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    print(format_suggestions(proposals, str(json_path), str(markdown_path)))
    return EXIT_OK


def _run_fix(args: argparse.Namespace) -> int:
    """Show or apply one suggestion, or undo one recorded application."""
    if args.rollback and args.apply:
        print(
            "reprocheck: error: --apply cannot be used with --rollback; rollback "
            "is already an explicit write operation.",
            file=sys.stderr,
        )
        return EXIT_OPERATIONAL
    try:
        if args.rollback:
            result = run_rollback(args.path, args.rollback)
        else:
            result = run_fix(args.path, args.suggestion, apply=args.apply)
    except ScanError as exc:
        print(f"reprocheck: error: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL
    except OSError as exc:
        print(f"reprocheck: error: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    project = str(result.project.get("path") or args.path)
    try:
        json_path = write_fix_json(
            result, resolve_destination(args.json, FIX_NAME, args.output_dir, project)
        )
        markdown_path = write_fix_markdown(
            result,
            resolve_destination(
                args.markdown, FIX_MARKDOWN_NAME, args.output_dir, project
            ),
        )
    except OSError as exc:
        print(
            f"reprocheck: error: could not write the fix report: {exc}", file=sys.stderr
        )
        return EXIT_OPERATIONAL

    print(format_fix(result, str(json_path), str(markdown_path)))
    return _fix_exit_code(result)


def _fix_exit_code(result: FixApplicationResult) -> int:
    if result.status in {
        ApplicationStatus.APPLIED,
        ApplicationStatus.DRY_RUN,
        ApplicationStatus.ROLLED_BACK,
    }:
        return EXIT_OK
    if result.status in {ApplicationStatus.STALE, ApplicationStatus.NOT_APPLICABLE}:
        return EXIT_STALE
    if result.status is ApplicationStatus.ROLLBACK_FAILED:
        return EXIT_ROLLBACK_FAILED
    return EXIT_ROLLED_BACK


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _write_reports(
    report: ScanReport, args: argparse.Namespace, project: str
) -> tuple[Path, Path]:
    """Write the JSON report (primary) and the Markdown report."""
    output = write_report(
        report, resolve_destination(args.json, REPORT_NAME, args.output_dir, project)
    )
    markdown = write_markdown(
        report,
        resolve_destination(
            args.markdown, REPORT_MARKDOWN_NAME, args.output_dir, project
        ),
    )
    return output, markdown


def _default_baseline_path(report: dict) -> Path:
    from reprocheck.output import project_output_dir

    project = (report.get("project") or {}).get("path")
    return project_output_dir(project) / BASELINE_NAME


def _exit_code(report: ScanReport) -> int:
    if report.verdict.status is VerdictStatus.FAIL:
        return EXIT_FAIL
    if report.verdict.status is VerdictStatus.PARTIAL:
        return EXIT_PARTIAL
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
