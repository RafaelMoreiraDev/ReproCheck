"""Scan orchestration: collect facts, then apply the deterministic checks.

The scan is read-only: nothing inside the analysed project is created,
modified, deleted or executed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from reprocheck import __version__
from reprocheck.checks import ALL_CHECKS, run_checks
from reprocheck.facts import Facts
from reprocheck.models import ScanReport
from reprocheck.scanners import (
    files,
    git,
    gitignore,
    packaging,
    paths,
    python_env,
    readme,
    tools,
)
from reprocheck.scanners.project import scan_project

REPORT_SCHEMA_VERSION = "1"


class ScanError(Exception):
    """Raised when the requested path cannot be scanned."""


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp with second precision."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def scan(path: str | Path) -> ScanReport:
    """Scan ``path`` in read-only mode and return a :class:`ScanReport`."""
    root = _resolve_target(path)
    facts = collect_facts(root)

    return ScanReport(
        reprocheck_version=__version__,
        scan_timestamp=utc_timestamp(),
        project=facts.project,
        git=facts.git,
        detected_files=facts.detected_files,
        python_requirements=facts.python_requirements,
        package_manager_hints=facts.package_manager_hints,
        readme_commands=facts.readme_commands,
        findings=run_checks(facts, ALL_CHECKS),
        report_schema_version=REPORT_SCHEMA_VERSION,
        facts=facts.to_dict(),
    )


def collect_facts(root: Path) -> Facts:
    """Run every read-only scanner and return the observed facts."""
    absolute_paths, file_references = paths.scan_paths(root)
    metadata = packaging.scan_project_metadata(root)
    return Facts(
        project=scan_project(root),
        git=git.scan_git(root),
        detected_files=files.scan_files(root),
        python_requirements=python_env.scan_python_requirements(root),
        package_manager_hints=packaging.scan_package_manager_hints(root),
        package_manager_signals=packaging.scan_package_manager_signals(root),
        readme_commands=readme.scan_readme_commands(root),
        readme_references=readme.scan_readme_references(root),
        absolute_paths=absolute_paths,
        file_references=file_references,
        tools=tools.scan_tools(root),
        distribution_name=metadata.name,
        declared_dependencies=metadata.dependencies,
        gitignore=gitignore.scan_gitignore(root),
    )


def _resolve_target(path: str | Path) -> Path:
    root = Path(path).expanduser()
    if not root.exists():
        raise ScanError(f"path does not exist: {root}")
    if not root.is_dir():
        raise ScanError(f"path is not a directory: {root}")
    return root.resolve()
