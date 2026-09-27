"""Plain-text terminal output."""

from __future__ import annotations

from collections import Counter

from reprocheck.models import Finding, ScanReport, Severity

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


def format_report(report: ScanReport, output: str, verbose: bool = False) -> str:
    """Render a compact human-readable summary of ``report``."""
    lines: list[str] = ["ReproCheck", ""]
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

    general = [
        item
        for item in report.findings
        if item.category not in {DEPENDENCY_CATEGORY, CI_REFERENCE_CATEGORY}
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
    lines.extend(_ci_reference_section(report))
    lines.extend(_findings_section(ci_references, title="CI findings"))
    lines.append("")
    lines.append("Report:")
    lines.append(f"  {output}")
    return "\n".join(lines)


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
