"""Markdown report writer.

The JSON document stays the structured source of truth; this renderer is a
projection of it for humans. It is deterministic (same report, same bytes),
evidence-first (every problem shows ID, severity, confidence, message,
evidence and location) and it omits sections that would be empty.
"""

from __future__ import annotations

from pathlib import Path

from reprocheck.checks.verdict import all_findings
from reprocheck.models.finding import Finding, Severity
from reprocheck.models.report import ScanReport
from reprocheck.reporters.recommendations import build_recommendations

DEFAULT_MARKDOWN_NAME = "reprocheck-report.md"

_SEVERITY_ORDER = (Severity.ERROR, Severity.WARNING, Severity.INFO)
_SEVERITY_TITLE = {
    Severity.ERROR: "Errors",
    Severity.WARNING: "Warnings",
    Severity.INFO: "Information",
}


def render_markdown(report: ScanReport) -> str:
    """Return the full Markdown document for ``report``."""
    sections = [
        _header(report),
        _summary(report),
        _what_worked(report),
        _problems(report),
        _not_verified(report),
        _environment(report),
        _reproduction(report),
        _ci(report),
        _dependencies(report),
        _conda(report),
        _files(report),
        _recommendations(report),
        _technical(report),
    ]
    body = "\n\n".join(section for section in sections if section)
    return f"{body}\n"


def write_markdown(report: ScanReport, destination: str | Path) -> Path:
    """Write the Markdown report and return the resolved output path."""
    path = Path(destination).expanduser()
    if path.is_dir():
        path = path / DEFAULT_MARKDOWN_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")
    return path.resolve()


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #


def _header(report: ScanReport) -> str:
    return (
        "# ReproCheck Report\n\n"
        "Read-only analysis of a Python project. "
        "This report only states what was observed; "
        "items under **Not verified** remain unknown."
    )


def _summary(report: ScanReport) -> str:
    verdict = report.verdict
    lines = [
        "## Summary",
        "",
        f"- Project: {report.project.name}",
        f"- Path: {report.project.path}",
        f"- Scan time: {report.scan_timestamp}",
        f"- ReproCheck version: {report.reprocheck_version}",
        f"- Reproduction verdict: **{verdict.status.value}**",
    ]
    if verdict.reasons:
        lines.append("- Why:")
        lines.extend(f"  - {reason}" for reason in verdict.reasons)
    return "\n".join(lines)


def _what_worked(report: ScanReport) -> str:
    facts = _positive_facts(report)
    if not facts:
        return ""
    lines = ["## What worked", ""]
    lines.extend(f"- {fact}" for fact in facts)
    return "\n".join(lines)


def _problems(report: ScanReport) -> str:
    findings = all_findings(report)
    if not findings:
        return "## Problems found\n\nNo finding was raised: every check that ran found no objective problem."
    blocks: list[str] = ["## Problems found"]
    for severity in _SEVERITY_ORDER:
        group = [item for item in findings if item.severity is severity]
        if not group:
            continue
        blocks.append(f"### {_SEVERITY_TITLE[severity]}")
        blocks.append("\n\n".join(_finding_block(item) for item in group))
    return "\n\n".join(blocks)


def _finding_block(finding: Finding) -> str:
    lines = [
        "```text",
        f"{finding.id} — {finding.severity.value.upper()} — "
        f"{finding.confidence.value.upper()} CONFIDENCE",
        "",
        finding.title + ".",
        "",
        finding.message,
    ]
    if finding.evidence:
        lines.extend(["", "Evidence:"])
        lines.extend(f"  {line}" for line in _wrap_evidence(finding.evidence))
    location = _location(finding)
    if location:
        lines.extend(["", f"Location: {location}"])
    lines.append("```")
    return "\n".join(lines)


def _location(finding: Finding) -> str | None:
    if finding.file and finding.line:
        return f"{finding.file}:{finding.line}"
    if finding.file:
        return str(finding.file)
    return None


def _wrap_evidence(evidence: str) -> list[str]:
    text = " | ".join(part.strip() for part in evidence.splitlines() if part.strip())
    return [line for line in text.split(" | ") if line]


def _not_verified(report: ScanReport) -> str:
    items = report.verdict.not_verified
    if not items:
        return ""
    lines = [
        "## Not verified",
        "",
        "These were not checked. They are not failures: the result is unknown.",
        "",
    ]
    lines.extend(f"- {item.item}: {item.reason}" for item in items)
    return "\n".join(lines)


def _environment(report: ScanReport) -> str:
    reproduction = report.reproduction
    lines = ["## Environment", ""]
    if reproduction:
        selection = reproduction.get("python") or {}
        venv = reproduction.get("venv") or {}
        lines.append(f"- Python: {selection.get('selected') or 'not selected'}")
        if venv.get("python_version"):
            lines.append(f"- Interpreter version: {venv.get('python_version')}")
    else:
        lines.append("- Python: not evaluated (no reproduction attempted)")
    for requirement in report.python_requirements:
        lines.append(f"- Declared {requirement.source}: {requirement.value}")
    if not report.python_requirements:
        lines.append("- Declared Python requirement: none found")
    lines.append(f"- Python files: {report.project.python_file_count}")
    lines.append(
        "- Network: "
        + (
            "enabled for the installation step"
            if reproduction.get("network_enabled")
            else "disabled (offline install attempted)"
            if reproduction
            else "not used"
        )
    )
    lines.append(
        "- Runtime checks: "
        + (
            "enabled (imports and test collection attempted)"
            if (reproduction.get("runtime_checks") or {}).get("enabled")
            else "not run"
        )
    )
    return "\n".join(lines)


def _reproduction(report: ScanReport) -> str:
    reproduction = report.reproduction
    if not reproduction:
        return ""
    installation = reproduction.get("installation") or {}
    pip_check = reproduction.get("pip_check") or {}
    distribution = reproduction.get("installed_distribution") or {}
    runtime = reproduction.get("runtime_checks") or {}
    collection = runtime.get("pytest_collection") or {}
    integrity = reproduction.get("integrity") or {}

    lines = ["## Reproduction", ""]
    if installation:
        lines.append(
            f"- Installation: {'PASS' if installation.get('success') else 'FAIL'} "
            f"(strategy: {installation.get('strategy')}, "
            f"exit {installation.get('exit_code')}, "
            f"{installation.get('duration_seconds')}s)"
        )
        if installation.get("stderr_path"):
            lines.append(f"- Installation log: `{installation.get('stderr_path')}`")
    else:
        lines.append("- Installation: not performed")
    if pip_check:
        lines.append(
            "- pip check: "
            + (
                "PASS (no broken requirements)"
                if pip_check.get("clean")
                else f"FAIL ({pip_check.get('conflict_count')} conflict(s))"
            )
        )
    if distribution.get("version"):
        note = (
            " — looks like a fallback version"
            if distribution.get("looks_like_fallback")
            else ""
        )
        lines.append(
            f"- Installed distribution: {distribution.get('name')} "
            f"{distribution.get('version')}{note}"
        )
    if runtime.get("enabled"):
        imports = runtime.get("imports") or []
        if imports:
            passed = sum(1 for item in imports if item.get("imported"))
            lines.append(
                f"- Imports: {passed}/{len(imports)} top-level modules imported successfully"
            )
            for item in imports:
                state = "ok" if item.get("imported") else "FAILED"
                detail = (
                    f" ({item.get('stderr_snippet')})"
                    if not item.get("imported") and item.get("stderr_snippet")
                    else ""
                )
                lines.append(f"  - `{item.get('module')}`: {state}{detail}")
        else:
            lines.append(
                "- Imports: none attempted ("
                f"{runtime.get('import_discovery') or 'no target'})"
            )
        if not collection.get("available"):
            lines.append(
                "- Pytest collection: not run — "
                + str(collection.get("reason") or "pytest is not installed")
            )
        elif collection.get("ran"):
            lines.append(
                "- Pytest collection: "
                + ("PASS" if collection.get("success") else "FAIL")
                + f" ({collection.get('collected')} test(s) collected, "
                f"exit {collection.get('exit_code')}; no test was executed)"
            )
    lines.append(
        "- Original source tree: "
        + (
            "unchanged"
            if reproduction.get("original_project_unchanged")
            else f"MODIFIED — changed paths: {', '.join(integrity.get('changed_paths') or [])}"
        )
    )
    return "\n".join(lines)


def _ci(report: ScanReport) -> str:
    references = report.workflow_references
    if not references:
        return ""
    external = [item for item in references if not item.get("is_local")]
    local = [item for item in references if item.get("is_local")]
    pinned = [item for item in external if item.get("is_sha")]
    mutable = [item for item in external if item.get("is_mutable")]
    lines = [
        "## CI",
        "",
        f"- Workflow files: {len(references)}",
        f"- External action references: {len(external)} "
        f"({len(pinned)} pinned to a full SHA, {len(mutable)} on a mutable reference)",
        f"- Local references: {len(local)}",
    ]
    for item in references:
        target = item.get("target") or item.get("raw")
        line = f":{item['line']}" if item.get("line") else ""
        lines.append(
            f"- `{target}` at `{item.get('ref')}` in `{item.get('file')}`{line}"
        )
    return "\n".join(lines)


def _dependencies(report: ScanReport) -> str:
    summary = (report.dependencies or {}).get("summary") or {}
    if not summary.get("unique_packages"):
        return ""
    lines = ["## Dependencies", ""]
    for key in ("runtime", "dev", "test", "optional", "build", "constraint"):
        if summary.get(key):
            lines.append(f"- {key}: {summary[key]}")
    lines.append(f"- Unique names: {summary.get('unique_packages', 0)}")
    signals = report.facts.get("package_manager_signals") or []
    for item in signals:
        lines.append(f"- {item['manager']} ({item['role']}): {item['evidence']}")
    return "\n".join(lines)


def _conda(report: ScanReport) -> str:
    """A short factual section, present only when an environment is declared.

    A summary and the findings, not a dump: a large environment has hundreds
    of packages and listing them would bury the part a reader needs.
    """
    section = report.conda or {}
    environments = section.get("environments") or []
    if not environments:
        return ""
    summary = section.get("summary") or {}
    lines = [
        "## Conda",
        "",
        "Static facts from the environment declarations. ReproCheck reads these "
        "files; it does not run conda, contact a channel, or build the "
        "environment.",
        "",
        f"- Environment files: {summary.get('environments', 0)}",
        f"- Conda dependencies: {summary.get('dependencies', 0)}",
        f"- pip requirements inside the environment: {summary.get('pip_dependencies', 0)}",
        f"- Distinct channels: {summary.get('channels', 0)}",
    ]
    for environment in environments:
        lines.extend(["", f"### {environment['file']}"])
        python = _conda_python(environment)
        lines.append(f"- Name: {environment.get('name') or '(not declared)'}")
        lines.append(f"- Python: {python or '(not declared)'}")
        channels = environment.get("channels") or []
        lines.append(
            "- Channels: " + (", ".join(channels) if channels else "(none declared)")
        )
        conda_deps = [
            item
            for item in environment.get("dependencies") or []
            if item.get("source") == "conda" and not item.get("is_python")
        ]
        pip_deps = [
            item
            for item in environment.get("dependencies") or []
            if item.get("source") == "conda-pip"
        ]
        lines.append(f"- Conda dependencies: {len(conda_deps)}")
        lines.append(f"- pip requirements: {len(pip_deps)}")
        conditional = [item for item in conda_deps if item.get("selector")]
        if conditional:
            names = ", ".join(
                f"{item['name']} [{'/'.join(item['selector'])}]"
                for item in conditional[:8]
            )
            more = "" if len(conditional) <= 8 else f", and {len(conditional) - 8} more"
            lines.append(f"- Platform-specific: {names}{more}")
        if environment.get("parse_error"):
            lines.append(f"- Not read: {environment['parse_error']}")
    findings = [item for item in report.findings if item.id.startswith("RC23")]
    if findings:
        lines.extend(["", "### Conda findings"])
        for finding in findings:
            location = finding.file or ""
            if finding.line:
                location = f"{location}:{finding.line}"
            lines.append(f"- `{finding.id}` ({finding.severity.value}) {location}")
            lines.append(f"  {finding.message}")
    return "\n".join(lines)


def _conda_python(environment: dict) -> str | None:
    for item in environment.get("dependencies") or []:
        if item.get("is_python"):
            spec = item.get("raw_spec")
            return f"{item.get('raw')}" + (f"  ({spec})" if spec else "")
    return None


def _files(report: ScanReport) -> str:
    if not report.detected_files:
        return ""
    lines = ["## Files and configuration", ""]
    for item in report.detected_files:
        kind = "directory" if item.kind == "directory" else "file"
        lines.append(f"- `{item.path}` ({item.category}, {kind})")
    if report.readme_commands:
        lines.append("")
        lines.append("Commands found in the README:")
        for command in report.readme_commands:
            lines.append(f"- `{command.command}` (`{command.file}:{command.line}`)")
    return "\n".join(lines)


def _recommendations(report: ScanReport) -> str:
    recommendations = build_recommendations(all_findings(report))
    if not recommendations:
        return ""
    lines = [
        "## Recommended next actions",
        "",
        "Deterministic suggestions derived from the findings above. "
        "ReproCheck changes nothing on its own.",
        "",
    ]
    for item in recommendations:
        lines.append(f"- **{item.finding_id}** — {item.action}")
    return "\n".join(lines)


def _technical(report: ScanReport) -> str:
    reproduction = report.reproduction
    lines = [
        "## Technical details",
        "",
        f"- Report schema version: {report.report_schema_version}",
        f"- ReproCheck version: {report.reprocheck_version}",
        f"- Scan timestamp: {report.scan_timestamp}",
    ]
    if reproduction:
        workspace = reproduction.get("workspace")
        lines.append(
            "- Workspace: "
            + (
                f"{workspace} (kept for inspection)"
                if workspace
                else "removed after a successful attempt"
            )
        )
        logs = [
            str(path)
            for path in (
                (reproduction.get("installation") or {}).get("stderr_path"),
                (reproduction.get("pip_check") or {}).get("stdout_path"),
            )
            if path
        ]
        if logs:
            state = "kept" if workspace else "removed with the workspace"
            lines.append(f"- Logs ({state}): " + ", ".join(f"`{p}`" for p in logs))
        steps = reproduction.get("completed_steps") or []
        if steps:
            lines.append("- Steps completed:")
            lines.extend(f"  - {step}" for step in steps)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Positive facts
# --------------------------------------------------------------------------- #


def _positive_facts(report: ScanReport) -> list[str]:
    """Only facts that were actually observed to succeed."""
    facts: list[str] = []
    git = report.git
    if git.is_repository and git.head and git.is_clean:
        facts.append(
            f"Git repository at commit {git.head[:12]} (branch "
            f"{git.branch or 'unknown'}, working tree clean)."
        )
    if report.project.python_file_count:
        facts.append(
            f"{report.project.python_file_count} Python file(s) were found and read."
        )

    reproduction = report.reproduction
    if not reproduction or not reproduction.get("attempted"):
        return facts

    selection = reproduction.get("python") or {}
    venv = reproduction.get("venv") or {}
    installation = reproduction.get("installation") or {}
    pip_check = reproduction.get("pip_check") or {}
    distribution = reproduction.get("installed_distribution") or {}
    runtime = reproduction.get("runtime_checks") or {}

    if selection.get("selected"):
        facts.append(f"Python {selection.get('selected')} was selected successfully.")
    if venv.get("created"):
        facts.append(
            "A virtual environment was created with Python "
            f"{venv.get('python_version') or 'the selected interpreter'}."
        )
    if installation.get("success"):
        facts.append(
            f"Project installation completed successfully "
            f"({installation.get('strategy')}, exit 0, "
            f"{installation.get('duration_seconds')}s)."
        )
    if pip_check.get("clean"):
        facts.append("pip check reported no broken requirements.")
    if distribution.get("version") and not distribution.get("looks_like_fallback"):
        facts.append(
            f"The installed distribution reports version {distribution.get('version')}."
        )
    if runtime.get("enabled"):
        imports = runtime.get("imports") or []
        if imports and all(item.get("imported") for item in imports):
            facts.append(
                f"{len(imports)} top-level module(s) imported successfully: "
                + ", ".join(f"`{item.get('module')}`" for item in imports)
                + "."
            )
        collection = runtime.get("pytest_collection") or {}
        if (
            collection.get("available")
            and collection.get("ran")
            and collection.get("success")
        ):
            facts.append(
                f"pytest collected {collection.get('collected')} test(s) without "
                "executing them."
            )
    if reproduction.get("original_project_unchanged"):
        facts.append("The original source tree remained unchanged.")
    return facts
