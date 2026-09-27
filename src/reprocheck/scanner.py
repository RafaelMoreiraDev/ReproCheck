"""Scan orchestration: run every read-only scanner and collect the results."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from reprocheck import __version__
from reprocheck.checks import run_checks
from reprocheck.models import ScanReport
from reprocheck.scanners import files, git, packaging, python_env, readme
from reprocheck.scanners.project import scan_project


class ScanError(Exception):
    """Raised when the requested path cannot be scanned."""


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp with second precision."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def scan(path: str | Path) -> ScanReport:
    """Scan ``path`` in read-only mode and return a :class:`ScanReport`."""
    root = Path(path).expanduser()
    if not root.exists():
        raise ScanError(f"path does not exist: {root}")
    if not root.is_dir():
        raise ScanError(f"path is not a directory: {root}")
    root = root.resolve()

    project = scan_project(root)
    git_info = git.scan_git(root)
    detected_files = files.scan_files(root)
    requirements = python_env.scan_python_requirements(root)
    hints = packaging.scan_package_manager_hints(root)
    commands = readme.scan_readme_commands(root)
    findings = run_checks(project, git_info, detected_files, requirements, hints)

    return ScanReport(
        reprocheck_version=__version__,
        scan_timestamp=utc_timestamp(),
        project=project,
        git=git_info,
        detected_files=detected_files,
        python_requirements=requirements,
        package_manager_hints=hints,
        readme_commands=commands,
        findings=findings,
    )
