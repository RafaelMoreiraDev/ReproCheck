"""Tests for the detection of well-known files, directories and workflows."""

from __future__ import annotations

from reprocheck.scanners import files


def test_detects_python_config_and_docs(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": "[project]\nname = 'x'\n",
            "requirements.txt": "requests\n",
            "uv.lock": "",
            "README.md": "# x\n",
            ".gitignore": "__pycache__/\n",
        }
    )
    detected = {item.path: item for item in files.scan_files(root)}

    assert detected["pyproject.toml"].kind == "file"
    assert detected["pyproject.toml"].category == "python-config"
    assert detected["requirements.txt"].category == "python-config"
    assert detected["uv.lock"].category == "python-config"
    assert detected["README.md"].category == "docs"
    assert detected[".gitignore"].category == "vcs"


def test_detects_directories(make_project) -> None:
    root = make_project({"tests/test_a.py": "", "src/pkg/__init__.py": ""})
    detected = {item.path: item for item in files.scan_files(root)}

    assert detected["tests"].kind == "directory"
    assert detected["tests"].category == "tests"
    assert detected["src"].kind == "directory"


def test_detects_workflows(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": "name: ci\n",
            ".github/workflows/release.yaml": "name: release\n",
            ".github/workflows/notes.txt": "ignored\n",
        }
    )
    workflows = files.scan_workflow_files(root)

    assert workflows == [
        ".github/workflows/ci.yml",
        ".github/workflows/release.yaml",
    ]
    assert all(item.category == "ci" for item in files.scan_files(root))


def test_absent_paths_are_not_reported(make_project) -> None:
    root = make_project({"a.py": ""})
    assert files.scan_files(root) == []
    assert files.scan_workflow_files(root) == []
