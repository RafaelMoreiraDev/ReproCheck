"""End-to-end tests for the scan orchestration and the JSON report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import requires_git
from reprocheck import __version__
from reprocheck.scanner import ScanError, scan

PROJECT = {
    "pyproject.toml": "[project]\nname = 'example'\nrequires-python = '>=3.11'\n",
    ".python-version": "3.11.4\n",
    "README.md": "# Example\n\n```bash\npip install -e .\npytest\n```\n",
    ".gitignore": "__pycache__/\n",
    "uv.lock": "",
    "requirements.txt": "requests\n",
    "src/example/__init__.py": "",
    "src/example/main.py": "",
    "tests/test_main.py": "def test_ok():\n    assert True\n",
    ".github/workflows/ci.yml": (
        "jobs:\n  test:\n    steps:\n      - uses: actions/setup-python@v5\n"
        "        with:\n          python-version: '3.11'\n"
    ),
}


def test_scan_requires_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(ScanError):
        scan(tmp_path / "missing")


def test_scan_rejects_files(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ScanError):
        scan(target)


def test_full_scan(make_project) -> None:
    report = scan(make_project(PROJECT))

    assert report.reprocheck_version == __version__
    assert report.project.name == "example"
    assert report.project.python_file_count == 3
    assert report.git.is_repository is False

    detected = {item.path for item in report.detected_files}
    assert {
        "pyproject.toml",
        ".python-version",
        "README.md",
        ".gitignore",
        "uv.lock",
        "requirements.txt",
        "src",
        "tests",
        ".github/workflows/ci.yml",
    } <= detected

    assert sorted((item.source, item.value) for item in report.python_requirements) == [
        (".github/workflows/ci.yml [python-version]", "3.11"),
        (".python-version", "3.11.4"),
        ("pyproject.toml [project.requires-python]", ">=3.11"),
    ]

    managers = {hint.manager for hint in report.package_manager_hints}
    assert {"uv", "pip"} <= managers

    assert [item.command for item in report.readme_commands] == [
        "pip install -e .",
        "pytest",
    ]

    ids = {finding.id for finding in report.findings}
    assert "RC004" in ids
    # uv.lock without any uv configuration, and a documented pip install that
    # cannot consume the uv lockfile.
    assert {"RC111", "RC114"} <= ids
    # setuptools-style signals alone must not be reported as a conflict.
    assert "RC009" not in ids
    assert "RC001" not in ids
    assert "RC002" not in ids
    assert "RC003" not in ids


@requires_git
def test_scan_of_git_project(git_project) -> None:
    report = scan(git_project(PROJECT))
    assert report.git.is_repository is True
    assert report.git.branch == "main"
    assert report.git.is_clean is True
    assert "RC004" not in {finding.id for finding in report.findings}


def test_json_report_is_written(tmp_path: Path, make_project) -> None:
    from reprocheck.reporters import write_report

    root = make_project(PROJECT)
    report = scan(root)
    destination = tmp_path / "out" / "report.json"
    written = write_report(report, destination)

    assert written == destination.resolve()
    data = json.loads(destination.read_text(encoding="utf-8"))
    assert list(data) == [
        "reprocheck_version",
        "report_schema_version",
        "scan_timestamp",
        "project",
        "git",
        "detected_files",
        "python_requirements",
        "package_manager_hints",
        "readme_commands",
        "findings",
        "dependencies",
        "workflow_references",
        "facts",
    ]
    assert data["reprocheck_version"] == __version__
    assert data["report_schema_version"] == "5"
    assert data["project"]["name"] == "example"
    assert data["git"]["is_repository"] is False
    assert data["findings"]
    assert set(data["dependencies"]) == {"declarations", "includes", "summary"}
    assert set(data["facts"]) == {
        "package_manager_signals",
        "readme_references",
        "absolute_paths",
        "file_references",
        "tools",
        "gitignore",
        "project_metadata",
    }


def test_report_is_deterministic_apart_from_timestamp(make_project) -> None:
    root = make_project(PROJECT)
    first = scan(root).to_dict()
    second = scan(root).to_dict()

    first.pop("scan_timestamp")
    second.pop("scan_timestamp")
    assert first == second


def test_json_contains_no_environment_secrets(make_project) -> None:
    root = make_project(PROJECT)
    payload = json.dumps(scan(root).to_dict()).lower()
    for forbidden in ("password", "token", "api_key", "secret", "home"):
        assert forbidden not in payload
