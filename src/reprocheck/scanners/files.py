"""Detection of well-known files, directories and CI workflows."""

from __future__ import annotations

from pathlib import Path

from reprocheck.models import DetectedFile
from reprocheck.scanners.manifest import (
    WATCH_DIRECTORIES,
    WATCH_FILES,
    WORKFLOW_DIR,
    WORKFLOW_SUFFIXES,
)


def scan_files(root: Path) -> list[DetectedFile]:
    """Return every watched path that exists under ``root``."""
    detected: list[DetectedFile] = []

    for relative, category in WATCH_FILES.items():
        if (root / relative).is_file():
            detected.append(DetectedFile(relative, "file", category))

    for relative, category in WATCH_DIRECTORIES.items():
        if (root / relative).is_dir():
            detected.append(DetectedFile(relative, "directory", category))

    detected.extend(_scan_workflows(root))
    return detected


def scan_workflow_files(root: Path) -> list[str]:
    """Return relative paths of GitHub Actions workflow files."""
    workflows = root / WORKFLOW_DIR
    if not workflows.is_dir():
        return []
    return sorted(
        f"{WORKFLOW_DIR}/{entry.name}"
        for entry in workflows.iterdir()
        if entry.is_file() and entry.suffix.lower() in WORKFLOW_SUFFIXES
    )


def _scan_workflows(root: Path) -> list[DetectedFile]:
    return [
        DetectedFile(relative, "file", "ci") for relative in scan_workflow_files(root)
    ]
