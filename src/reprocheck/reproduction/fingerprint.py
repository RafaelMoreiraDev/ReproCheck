"""Integrity fingerprint of the analysed project.

The reproduction runs on a copy, so the original must not change. The
fingerprint is taken before the attempt and compared afterwards; any difference
is a ReproCheck error, not a finding about the project.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from reprocheck.reproduction.models import FingerprintResult

#: Never followed: version control metadata and environments are excluded so the
#: fingerprint is stable and cheap.
SKIP_DIRS = frozenset({".git", ".hg", ".svn", ".venv", "venv", "node_modules"})

MAX_FILES = 20000


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """Size and modification time of every file, plus the Git state."""

    files: dict[str, tuple[int, int]]
    git_head: str | None
    git_status: str | None

    @property
    def count(self) -> int:
        return len(self.files)


def capture(root: Path) -> Fingerprint:
    """Fingerprint ``root`` without writing anything."""
    files: dict[str, tuple[int, int]] = {}
    for current, dirnames, filenames in os.walk(root, onerror=None):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            try:
                stat = path.stat()
            except OSError:  # pragma: no cover - defensive
                continue
            files[relative] = (stat.st_size, stat.st_mtime_ns)
            if len(files) >= MAX_FILES:
                break
        if len(files) >= MAX_FILES:
            break
    return Fingerprint(
        files=files,
        git_head=_git(root, "rev-parse", "HEAD"),
        git_status=_git(root, "status", "--porcelain"),
    )


def compare(before: Fingerprint, after: Fingerprint) -> FingerprintResult:
    """Compare two fingerprints and list what changed."""
    changed: list[str] = []
    for path, value in before.files.items():
        other = after.files.get(path)
        if other is None:
            changed.append(f"removed: {path}")
        elif other != value:
            changed.append(f"modified: {path}")
    for path in after.files:
        if path not in before.files:
            changed.append(f"created: {path}")
    head_changed = before.git_head != after.git_head
    status_changed = before.git_status != after.git_status
    if head_changed:
        changed.append("git HEAD changed")
    if status_changed:
        changed.append("git status changed")

    return FingerprintResult(
        captured_before=True,
        unchanged=not changed,
        git_head_before=before.git_head,
        git_head_after=after.git_head,
        git_status_before=before.git_status,
        git_status_after=after.git_status,
        tracked_files=before.count,
        changed_paths=tuple(sorted(changed)),
    )


def digest(root: Path, relative: str) -> str | None:
    """SHA-256 of one file, used for spot checks in the report."""
    path = root / relative
    if not path.is_file():
        return None
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                hasher.update(chunk)
    except OSError:  # pragma: no cover - defensive
        return None
    return hasher.hexdigest()


def _git(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603
            ["git", "-C", str(root), "--no-optional-locks", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()
