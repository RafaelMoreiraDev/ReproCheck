"""Tests for the project scanner."""

from __future__ import annotations

from pathlib import Path

from reprocheck.scanners import project


def test_project_name_and_absolute_path(make_project) -> None:
    root = make_project({"pkg/__init__.py": "", "pkg/main.py": ""})
    result = project.scan_project(root)

    assert result.name == "example"
    assert Path(result.path).is_absolute()
    assert Path(result.path) == root.resolve()
    assert result.exists is True


def test_python_file_count(make_project) -> None:
    root = make_project(
        {
            "a.py": "",
            "pkg/b.py": "",
            "pkg/c.py": "",
            "README.md": "",
        }
    )
    assert project.scan_project(root).python_file_count == 3


def test_noisy_directories_are_ignored(make_project) -> None:
    root = make_project(
        {
            "a.py": "",
            ".venv/lib/b.py": "",
            "build/c.py": "",
            "node_modules/d.py": "",
            "__pycache__/e.py": "",
        }
    )
    assert project.scan_project(root).python_file_count == 1


def test_missing_path_is_reported_as_non_existing(tmp_path: Path) -> None:
    result = project.scan_project(tmp_path / "nope")
    assert result.exists is False
    assert result.python_file_count == 0
