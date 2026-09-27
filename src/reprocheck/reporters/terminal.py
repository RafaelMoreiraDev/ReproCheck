"""Plain-text terminal output."""

from __future__ import annotations

from reprocheck.models import ScanReport, Severity

_SEVERITY_LABEL = {
    Severity.INFO: "INFO",
    Severity.WARNING: "WARN",
    Severity.ERROR: "ERROR",
}

_CATEGORY_LABEL = {
    "ci": "GitHub Actions",
    "docs": "README",
    "python-config": "Python config",
    "source": "src/",
    "tests": "tests/",
    "vcs": ".gitignore",
}


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
        lines.append("")
        lines.append("Package managers")
        if report.package_manager_hints:
            lines.extend(
                f"  {hint.manager}: {hint.evidence}"
                for hint in report.package_manager_hints
            )
        else:
            lines.append("  (no signals)")
        lines.append("")
        lines.append("README commands")
        if report.readme_commands:
            lines.extend(
                f"  {item.file}:{item.line}  {item.command}"
                for item in report.readme_commands
            )
        else:
            lines.append("  (none found)")
    lines.append("")

    lines.append("Findings")
    if report.findings:
        for finding in report.findings:
            label = _SEVERITY_LABEL[finding.severity]
            lines.append(f"  {label} {finding.id} {finding.message}")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("Report:")
    lines.append(f"  {output}")
    return "\n".join(lines)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _tri_state(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "yes" if value else "no"
