"""Shared fixtures: temporary projects and filesystem snapshots."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

GIT_AVAILABLE = shutil.which("git") is not None
requires_git = pytest.mark.skipif(not GIT_AVAILABLE, reason="git is not installed")

ProjectFactory = Callable[..., Path]


def _write(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


@pytest.fixture
def make_project(tmp_path: Path) -> ProjectFactory:
    """Return a factory that materialises a project tree from a file mapping."""

    def factory(files: dict[str, str] | None = None, name: str = "example") -> Path:
        root = tmp_path / name
        root.mkdir(parents=True, exist_ok=True)
        if files:
            _write(root, files)
        return root

    return factory


@pytest.fixture
def git_project(make_project: ProjectFactory) -> ProjectFactory:
    """Same as ``make_project`` but initialised as a Git repository."""

    def factory(files: dict[str, str] | None = None, name: str = "example") -> Path:
        root = make_project(files, name=name)
        run_git(root, "init", "-b", "main")
        run_git(root, "config", "user.email", "test@example.com")
        run_git(root, "config", "user.name", "ReproCheck Test")
        run_git(root, "add", "-A")
        run_git(root, "commit", "-m", "initial")
        return root

    return factory


@pytest.fixture
def finding_ids(make_project: ProjectFactory) -> Callable[[dict[str, str]], set[str]]:
    """Return a helper that scans a temporary project and yields finding IDs."""
    from reprocheck.scanner import scan

    def helper(files: dict[str, str], name: str = "example") -> set[str]:
        root = make_project(files, name=name)
        return {finding.id for finding in scan(root).findings}

    return helper


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a Git command inside ``root`` (test setup only)."""
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def snapshot(root: Path, exclude: Iterable[str] = ()) -> dict[str, tuple[int, str]]:
    """Map every file under ``root`` to ``(size, sha256)``."""
    excluded = {".git"}
    excluded.update(exclude)
    result: dict[str, tuple[int, str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.split("/")[0] in excluded:
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        result[relative] = (len(data), digest)
    return result
