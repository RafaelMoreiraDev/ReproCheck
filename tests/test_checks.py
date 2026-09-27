"""Tests for the objective findings."""

from __future__ import annotations

from reprocheck.checks import run_checks
from reprocheck.models import (
    DetectedFile,
    GitInfo,
    PackageManagerHint,
    ProjectScan,
    PythonRequirement,
    Severity,
)


def _checks(**overrides):
    defaults = {
        "project": ProjectScan(name="x", path="C:/x", python_file_count=3),
        "git": GitInfo(is_repository=True, branch="main", is_clean=True),
        "detected_files": [
            DetectedFile("pyproject.toml", "file", "python-config"),
            DetectedFile("README.md", "file", "docs"),
            DetectedFile(".gitignore", "file", "vcs"),
            DetectedFile("tests", "directory", "tests"),
            DetectedFile(".github/workflows/ci.yml", "file", "ci"),
        ],
        "python_requirements": [PythonRequirement(".python-version", "3.11")],
        "package_manager_hints": [PackageManagerHint("uv", "uv.lock")],
    }
    defaults.update(overrides)
    return run_checks(**defaults)


def _ids(**overrides) -> set[str]:
    return {finding.id for finding in _checks(**overrides)}


def test_complete_project_has_no_findings() -> None:
    assert _ids() == set()


def test_rc001_missing_readme() -> None:
    files = [
        DetectedFile("pyproject.toml", "file", "python-config"),
        DetectedFile("tests", "directory", "tests"),
        DetectedFile(".gitignore", "file", "vcs"),
        DetectedFile(".github/workflows/ci.yml", "file", "ci"),
    ]
    result = _ids(detected_files=files)
    assert "RC001" in result


def test_rc002_missing_python_version() -> None:
    assert "RC002" in _ids(python_requirements=[])


def test_rc003_missing_python_config() -> None:
    files = [
        DetectedFile("README.md", "file", "docs"),
        DetectedFile(".gitignore", "file", "vcs"),
        DetectedFile("tests", "directory", "tests"),
        DetectedFile(".github/workflows/ci.yml", "file", "ci"),
    ]
    assert "RC003" in _ids(detected_files=files)


def test_rc004_not_a_repository() -> None:
    findings = _checks(git=GitInfo(is_repository=False))
    finding = next(f for f in findings if f.id == "RC004")
    assert finding.severity is Severity.WARNING
    assert finding.category == "vcs"


def test_rc005_dirty_working_tree() -> None:
    findings = _checks(git=GitInfo(is_repository=True, is_clean=False, dirty_entries=2))
    finding = next(f for f in findings if f.id == "RC005")
    assert finding.severity is Severity.INFO
    assert "2 changed" in finding.message


def test_rc006_rc007_rc008() -> None:
    files = [DetectedFile("README.md", "file", "docs")]
    result = _ids(detected_files=files)
    assert {"RC003", "RC006", "RC007", "RC008"} <= result


def test_rc009_multiple_managers() -> None:
    hints = [
        PackageManagerHint("uv", "uv.lock"),
        PackageManagerHint("poetry", "poetry.lock"),
    ]
    finding = next(f for f in _checks(package_manager_hints=hints) if f.id == "RC009")
    assert finding.severity is Severity.INFO
    assert "poetry, uv" in finding.message


def test_rc010_no_python_files() -> None:
    project = ProjectScan(name="x", path="C:/x", python_file_count=0)
    assert "RC010" in _ids(project=project)


def test_finding_serialisation_shape() -> None:
    finding = next(
        f for f in _checks(git=GitInfo(is_repository=False)) if f.id == "RC004"
    )
    assert set(finding.to_dict()) == {
        "id",
        "title",
        "severity",
        "category",
        "message",
        "evidence",
        "file",
        "line",
    }
