"""Tests for the read-only Git scanner."""

from __future__ import annotations

from pathlib import Path

from conftest import requires_git, run_git
from reprocheck.scanners import git


@requires_git
def test_clean_repository(git_project) -> None:
    root = git_project({"pyproject.toml": "[project]\nname='x'\n"})
    info = git.scan_git(root)

    assert info.is_repository is True
    assert info.branch == "main"
    assert info.head and len(info.head) == 40
    assert info.is_clean is True
    assert info.dirty_entries == 0


@requires_git
def test_dirty_repository(git_project) -> None:
    root = git_project({"a.py": "print(1)\n"})
    (root / "a.py").write_text("print(2)\n", encoding="utf-8")

    info = git.scan_git(root)
    assert info.is_repository is True
    assert info.is_clean is False
    assert info.dirty_entries == 1


@requires_git
def test_directory_without_git(make_project) -> None:
    root = make_project({"a.py": ""})
    info = git.scan_git(root)
    assert info.is_repository is False
    assert info.branch is None


@requires_git
def test_subdirectory_of_repository_is_not_a_repository_root(git_project) -> None:
    root = git_project({"a.py": ""})
    nested = root / "nested"
    nested.mkdir()

    info = git.scan_git(nested)
    assert info.is_repository is False
    assert info.toplevel is not None
    assert Path(info.toplevel).name == root.name


def test_non_directory(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    assert git.scan_git(target).is_repository is False


@requires_git
def test_detached_head(git_project) -> None:
    root = git_project({"a.py": ""})
    head = run_git(root, "rev-parse", "HEAD").stdout.strip()
    run_git(root, "checkout", "--detach", head)

    info = git.scan_git(root)
    assert info.is_repository is True
    assert info.branch is None
    assert info.head == head
