"""Plain-text terminal output."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from reprocheck.models import Finding, ScanReport, Severity

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from reprocheck.diff.models import ReproducibilityDiff
    from reprocheck.fix.models import FixApplicationResult
    from reprocheck.suggest.models import SuggestionReport

_SEVERITY_LABEL = {
    Severity.ERROR: "ERROR",
    Severity.WARNING: "WARN",
    Severity.INFO: "INFO",
}

_SEVERITY_ORDER = (Severity.ERROR, Severity.WARNING, Severity.INFO)

_SEVERITY_PLURAL = {
    Severity.ERROR: "errors",
    Severity.WARNING: "warnings",
    Severity.INFO: "info",
}

_CATEGORY_LABEL = {
    "ci": "GitHub Actions",
    "docs": "README",
    "python-config": "Python config",
    "source": "src/",
    "tests": "tests/",
    "vcs": ".gitignore",
}

#: Findings of this category are listed in their own terminal section.
DEPENDENCY_CATEGORY = "dependencies"
CI_REFERENCE_CATEGORY = "ci-references"
CONDA_CATEGORY = "conda"


def _conda_section(report: ScanReport) -> list[str]:
    """The Conda block: counts, then the declarations, then the Python pin."""
    summary = report.conda.get("summary") or {}
    lines = [
        "Conda",
        f"  environment files: {summary.get('environments', 0)}",
        f"  dependencies: {summary.get('dependencies', 0)}",
        f"  pip dependencies: {summary.get('pip_dependencies', 0)}",
        f"  channels: {summary.get('channels', 0)}",
    ]
    for environment in report.conda.get("environments") or []:
        lines.append(f"  - {environment['file']}")
        if environment.get("name"):
            lines.append(f"      name: {environment['name']}")
        python = next(
            (
                item
                for item in environment.get("dependencies") or []
                if item.get("is_python")
            ),
            None,
        )
        lines.append(f"      python: {python['raw'] if python else '(not declared)'}")
        channels = environment.get("channels") or []
        if channels:
            lines.append(f"      channels: {', '.join(channels)}")
        if environment.get("parse_error"):
            lines.append(f"      not read: {environment['parse_error']}")
    return lines


def format_report(
    report: ScanReport,
    output: str,
    verbose: bool = False,
    markdown: str | None = None,
) -> str:
    """Render a compact human-readable summary of ``report``."""
    lines: list[str] = ["ReproCheck", ""]
    lines.append(f"Verdict: {report.verdict.status.value}")
    for reason in report.verdict.reasons:
        lines.append(f"  - {reason}")
    lines.append("")
    lines.append(f"Project: {report.project.name}")
    lines.append(f"Path: {report.project.path}")
    lines.append(f"Python files: {report.project.python_file_count}")
    lines.append("")

    lines.append("Git")
    lines.append(f"  repository: {_yes_no(report.git.is_repository)}")
    lines.append(f"  branch: {report.git.branch or '-'}")
    if verbose:
        lines.append(f"  head: {report.git.head or '-'}")
    lines.append(f"  clean: {_tri_state(report.git.is_clean)}")
    lines.append("")

    lines.append("Python")
    if report.python_requirements:
        lines.extend(
            f"  {item.source}: {item.value}" for item in report.python_requirements
        )
    else:
        lines.append("  (none declared)")
    lines.append("")

    lines.append("Detected")
    if report.detected_files:
        for item in report.detected_files:
            label = _CATEGORY_LABEL.get(item.category, item.category)
            suffix = "/" if item.kind == "directory" else ""
            lines.append(f"  [OK] {item.path}{suffix}  ({label})")
    else:
        lines.append("  (nothing detected)")
    if verbose:
        lines.extend(_verbose_sections(report))
    lines.append("")

    lines.extend(_dependency_section(report))
    lines.append("")

    if report.conda.get("environments"):
        lines.extend(_conda_section(report))
        lines.append("")

    general = [
        item
        for item in report.findings
        if item.category
        not in {DEPENDENCY_CATEGORY, CI_REFERENCE_CATEGORY, CONDA_CATEGORY}
    ]
    dependency = [
        item for item in report.findings if item.category == DEPENDENCY_CATEGORY
    ]
    ci_references = [
        item for item in report.findings if item.category == CI_REFERENCE_CATEGORY
    ]
    lines.extend(_findings_section(general))
    lines.append("")
    lines.extend(_findings_section(dependency, title="Dependency findings"))
    lines.append("")
    conda_findings = [
        item for item in report.findings if item.category == CONDA_CATEGORY
    ]
    if conda_findings:
        lines.extend(_findings_section(conda_findings, title="Conda findings"))
        lines.append("")
    lines.extend(_ci_reference_section(report))
    lines.extend(_findings_section(ci_references, title="CI findings"))
    if report.reproduction:
        lines.append("")
        lines.extend(_reproduction_section(report, verbose=verbose))
    if report.verdict.not_verified:
        lines.append("")
        lines.append("Not verified (unknown, not failed):")
        lines.extend(f"  - {item.item}" for item in report.verdict.not_verified)
    lines.append("")
    lines.append("Report:")
    lines.append(f"  {output}")
    if markdown:
        lines.append(f"  {markdown}")
    return "\n".join(lines)


def _reproduction_section(report: ScanReport, *, verbose: bool) -> list[str]:
    data = report.reproduction
    integrity = data.get("integrity") or {}
    installation = data.get("installation") or {}
    pip_check = data.get("pip_check") or {}
    venv = data.get("venv") or {}
    selection = data.get("python") or {}

    lines = ["Reproduction"]
    lines.append(f"  attempted: {_yes_no(bool(data.get('attempted')))}")
    lines.append(
        f"  network: {'enabled' if data.get('network_enabled') else 'disabled'}"
    )
    lines.append(
        f"  runtime checks: "
        f"{'enabled' if data.get('runtime_checks_enabled') else 'disabled'}"
    )
    lines.append(
        f"  python: {selection.get('selected') or '-'} "
        f"({selection.get('reason') or 'no selection'})"
    )
    if venv:
        lines.append(f"  venv: {venv.get('path') or '-'}")
    if installation:
        lines.append(
            f"  strategy: {installation.get('strategy')} "
            f"(exit {installation.get('exit_code')}, "
            f"{installation.get('duration_seconds')}s)"
        )
        lines.append(f"  cwd: {installation.get('cwd')}")
    if pip_check:
        lines.append(
            f"  pip check: "
            f"{'clean' if pip_check.get('clean') else pip_check.get('conflict_count') or 'not run'}"
        )
    lines.append(
        f"  original project unchanged: "
        f"{_yes_no(bool(data.get('original_project_unchanged')))}"
    )
    lines.extend(_runtime_section(data))
    lines.append(
        f"  workspace: {data.get('workspace') or 'removed'}"
        + (" (kept)" if data.get("workspace_kept") else "")
    )
    if verbose:
        lines.append(f"  source copy: {data.get('source')}")
        if installation:
            lines.append(f"  install log: {installation.get('stderr_path')}")
        if pip_check:
            lines.append(f"  pip check log: {pip_check.get('stdout_path')}")
        steps = data.get("completed_steps") or []
        if steps:
            lines.append("  steps:")
            lines.extend(f"    - {step}" for step in steps)
    if integrity.get("changed_paths"):
        lines.append("  changed paths:")
        lines.extend(f"    - {item}" for item in integrity["changed_paths"][:10])  # type: ignore[index]
    lines.extend(
        _findings_section(report.reproduction_findings, title="Reproduction findings")
    )
    return lines


def _ci_reference_section(report: ScanReport) -> list[str]:
    references = report.workflow_references
    if not references:
        return []
    external = [item for item in references if not item.get("is_local")]
    local = [item for item in references if item.get("is_local")]
    pinned = [item for item in external if item.get("is_sha")]
    mutable = [item for item in external if item.get("is_mutable")]
    return [
        "CI References",
        f"  external: {len(external)}",
        f"  full-SHA pinned: {len(pinned)}",
        f"  mutable refs: {len(mutable)}",
        f"  local: {len(local)}",
        "",
    ]


def _runtime_section(data: dict) -> list[str]:
    """Installed version, import smoke test and test collection."""
    distribution = data.get("installed_distribution") or {}
    runtime_checks = data.get("runtime_checks") or {}
    lines: list[str] = []
    if distribution.get("version"):
        note = (
            " (looks like a fallback)"
            if distribution.get("looks_like_fallback")
            else ""
        )
        lines.append(
            f"  installed: {distribution.get('name')} "
            f"{distribution.get('version')}{note}"
        )
    if not runtime_checks.get("enabled"):
        return lines

    imports = runtime_checks.get("imports") or []
    if imports:
        for item in imports:
            status = "ok" if item.get("imported") else "FAILED"
            lines.append(f"  import {item.get('module')}: {status}")
    else:
        reason = runtime_checks.get("import_discovery") or "no target"
        lines.append(f"  import target: not determined ({reason})")

    collection = runtime_checks.get("pytest_collection") or {}
    if not collection.get("available"):
        lines.append("  pytest: not installed, collection skipped")
    elif collection.get("ran"):
        collected = collection.get("collected")
        lines.append(
            "  pytest collection: "
            + (
                "ok"
                if collection.get("success")
                else f"FAILED (exit {collection.get('exit_code')})"
            )
            + (f", {collected} item(s)" if collected is not None else "")
        )
    return lines


def _dependency_section(report: ScanReport) -> list[str]:
    summary = report.dependencies.get("summary") or {}
    if not summary.get("unique_packages"):
        return []
    lines = ["Dependencies"]
    for key in ("runtime", "dev", "test", "optional", "build", "constraint"):
        if summary.get(key):
            lines.append(f"  {key}: {summary[key]}")
    lines.append(f"  unique: {summary.get('unique_packages', 0)}")
    return lines


def _verbose_sections(report: ScanReport) -> list[str]:
    signals = report.facts.get("package_manager_signals") or []
    lines = ["", "Package managers"]
    if signals:
        lines.extend(
            f"  {item['manager']} ({item['role']}): {item['evidence']}"
            for item in signals  # type: ignore[index]
        )
    else:
        lines.append("  (no signals)")

    tools = report.facts.get("tools") or []
    lines.extend(["", "Tools"])
    if tools:
        lines.extend(
            f"  {item['name']}: {item['evidence']}"
            for item in tools  # type: ignore[index]
        )
    else:
        lines.append("  (none detected)")

    lines.extend(["", "README commands"])
    if report.readme_commands:
        lines.extend(
            f"  {item.file}:{item.line}  {item.command}"
            for item in report.readme_commands
        )
    else:
        lines.append("  (none found)")
    return lines


def _findings_section(findings: list[Finding], title: str = "Findings") -> list[str]:
    counts = Counter(finding.severity for finding in findings)
    lines = [title]
    for severity in _SEVERITY_ORDER:
        lines.append(f"  {counts.get(severity, 0)} {_SEVERITY_PLURAL[severity]}")
    if not findings:
        return lines + ["", "  (none)"]

    for severity in _SEVERITY_ORDER:
        group = [item for item in findings if item.severity is severity]
        if not group:
            continue
        lines.append("")
        for finding in group:
            lines.append(
                f"  {_SEVERITY_LABEL[severity]} {finding.id} {finding.message}"
            )
    return lines


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _tri_state(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "yes" if value else "no"


# --------------------------------------------------------------------------- #
# Baseline comparison
# --------------------------------------------------------------------------- #


def format_diff(
    diff: ReproducibilityDiff,
    json_path: str,
    markdown_path: str,
    baseline: str = "",
    current: str = "",
) -> str:
    """Render a baseline comparison for the terminal."""
    lines = ["ReproCheck baseline comparison", ""]
    if baseline:
        lines.append(f"Baseline: {baseline}")
    if current:
        lines.append(f"Current:  {current}")
    lines.append("")
    lines.append("Verdict:")
    if diff.verdict.changed:
        lines.append(
            f"  {diff.verdict.previous or '(none)'} -> {diff.verdict.current or '(none)'}"
        )
    else:
        lines.append(f"  {diff.verdict.current or '(none)'} (no change)")
    lines.append("")

    if not diff.has_changes:
        lines.append("No material reproducibility changes detected.")
        lines.append("")
        lines.append("Report:")
        lines.append(f"  {json_path}")
        lines.append(f"  {markdown_path}")
        return "\n".join(lines)

    lines.append("Changes:")
    lines.extend(_diff_counts(diff))
    lines.append("")
    lines.append("Report:")
    lines.append(f"  {json_path}")
    lines.append(f"  {markdown_path}")
    return "\n".join(lines)


def _diff_counts(diff: ReproducibilityDiff) -> list[str]:
    lines = [
        f"  {len(diff.findings.added)} finding(s) added",
        f"  {len(diff.findings.resolved)} finding(s) resolved",
        f"  {len(diff.findings.changed)} finding(s) changed",
    ]
    if diff.dependencies:
        lines.append(f"  {len(diff.dependencies)} dependency change(s)")
    if diff.python:
        lines.append(f"  {len(diff.python)} Python declaration change(s)")
    if diff.ci:
        lines.append(f"  {len(diff.ci)} CI reference change(s)")
    if diff.reproduction:
        lines.append(f"  {len(diff.reproduction)} reproduction fact change(s)")
    return lines


# --------------------------------------------------------------------------- #
# Fix suggestions
# --------------------------------------------------------------------------- #


def format_suggestions(
    report: SuggestionReport, json_path: str, markdown_path: str
) -> str:
    """Render the suggestion summary for the terminal.

    The line before the paths is not decoration: it is the only thing that tells
    the reader the diff they are about to read was never applied.
    """
    summary = report.summary
    lines = ["ReproCheck suggestions", ""]
    lines.append(f"Safe fixes:       {summary.fix_available}")
    lines.append(f"Review required:  {summary.review_required}")
    lines.append(f"Manual only:      {summary.manual_only}")
    lines.append(f"With a patch:     {summary.patches}")
    lines.append(f"No proposal:      {summary.no_proposal}")
    lines.append("")
    for item in report.safe:
        lines.append(f"  [{item.suggestion_id}] {item.title}")
        if item.file:
            lines.append(f"      file: {item.file}")
    if report.safe:
        lines.append("")
    lines.append("No files were modified.")
    lines.append("")
    lines.append("Report:")
    lines.append(f"  {json_path}")
    lines.append(f"  {markdown_path}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Fix
# --------------------------------------------------------------------------- #


def format_fix(result: FixApplicationResult, json_path: str, markdown_path: str) -> str:
    """Render one fix invocation for the terminal.

    The wording never claims the project is fixed: one suggestion was either
    shown or written, and every other finding is untouched.
    """
    lines = ["ReproCheck fix", ""]
    lines.append("Suggestion:")
    lines.append(f"  {result.suggestion_id or result.requested or '-'}")
    lines.append("")
    lines.append("File:")
    lines.append(f"  {result.file or '-'}")
    lines.append("")
    lines.append("Safety:")
    lines.append(f"  {result.safety or '-'}")
    lines.append("")
    if result.before_sha256:
        state = "PASS" if result.precondition_passed else "FAIL"
        lines.append("Precondition:")
        lines.append(f"  {state}")
        lines.append(f"  before: {result.before_sha256}")
        lines.append(f"  after:  {result.after_sha256 or '-'}")
        lines.append("")
    lines.append("Status:")
    lines.append(f"  {result.status.value}")
    lines.append(f"  Applied: {'YES' if result.applied else 'NO'}")
    if result.validation.performed:
        lines.append("")
        lines.append("Validation:")
        lines.append(f"  {'PASS' if result.validation.finding_resolved else 'FAILED'}")
        if result.validation.detail:
            lines.append(f"  {result.validation.detail}")
    if result.rollback.value != "NOT_NEEDED":
        lines.append("")
        lines.append("Rollback:")
        lines.append(f"  {result.rollback.value}")
    if result.record_path:
        lines.append("")
        lines.append("Record:")
        lines.append(f"  {result.record_path}")
    if result.reason:
        lines.append("")
        lines.append(f"Reason: {result.reason}")
    lines.extend(result.messages)
    lines.append("")
    if result.unified_diff:
        lines.append("Diff:")
        lines.extend(result.unified_diff.splitlines())
        lines.append("")
    lines.append("Report:")
    lines.append(f"  {json_path}")
    lines.append(f"  {markdown_path}")
    return "\n".join(lines)
