"""Tests for the V0.1 objective checks (RC001-RC010)."""

from __future__ import annotations

from reprocheck.checks import run_checks
from reprocheck.facts import (
    ROLE_INSTALLER,
    ROLE_LOCKFILE,
    SOURCE_CONFIG,
    Facts,
    GitignoreInfo,
    PackageManagerSignal,
)
from reprocheck.models import (
    Confidence,
    DetectedFile,
    GitInfo,
    PackageManagerHint,
    ProjectScan,
    PythonRequirement,
    Severity,
)

_COMPLETE_FILES = [
    DetectedFile("pyproject.toml", "file", "python-config"),
    DetectedFile("README.md", "file", "docs"),
    DetectedFile(".gitignore", "file", "vcs"),
    DetectedFile("tests", "directory", "tests"),
    DetectedFile(".github/workflows/ci.yml", "file", "ci"),
]


def _facts(**overrides) -> Facts:
    defaults: dict[str, object] = {
        "project": ProjectScan(name="x", path="C:/x", python_file_count=3),
        "git": GitInfo(is_repository=True, branch="main", is_clean=True),
        "detected_files": list(_COMPLETE_FILES),
        "python_requirements": [PythonRequirement(".python-version", "3.11")],
        "package_manager_hints": [PackageManagerHint("uv", "uv.lock")],
        "package_manager_signals": [
            PackageManagerSignal(
                "uv", ROLE_INSTALLER, "pyproject.toml [tool.uv]", SOURCE_CONFIG
            )
        ],
        "gitignore": GitignoreInfo(
            exists=True,
            patterns=("__pycache__/", ".venv/", "build/", "dist/", "*.egg-info/"),
        ),
    }
    defaults.update(overrides)
    return Facts(**defaults)  # type: ignore[arg-type]


def _ids(**overrides) -> set[str]:
    return {finding.id for finding in run_checks(_facts(**overrides))}


def _basic_ids(**overrides) -> set[str]:
    """Only the V0.1 checks, to keep these tests focused."""
    from reprocheck.checks import basic

    names = {
        finding.id
        for check in (
            basic.check_readme,
            basic.check_python_version,
            basic.check_python_config,
            basic.check_git,
            basic.check_tests,
            basic.check_workflows,
            basic.check_gitignore,
            basic.check_package_managers,
            basic.check_python_files,
        )
        for finding in [check(_facts(**overrides))]  # type: ignore[misc]
        if finding is not None
    }
    return names


def test_complete_project_has_no_basic_findings() -> None:
    assert _basic_ids() == set()


def test_rc001_missing_readme() -> None:
    files = [item for item in _COMPLETE_FILES if item.category != "docs"]
    assert "RC001" in _basic_ids(detected_files=files)


def test_rc002_missing_python_version() -> None:
    assert "RC002" in _basic_ids(python_requirements=[])


def test_rc003_missing_python_config() -> None:
    files = [item for item in _COMPLETE_FILES if item.category != "python-config"]
    assert "RC003" in _basic_ids(detected_files=files)


def test_rc004_not_a_repository() -> None:
    finding = next(
        item
        for item in run_checks(_facts(git=GitInfo(is_repository=False)))
        if item.id == "RC004"
    )
    assert finding.severity is Severity.WARNING
    assert finding.category == "vcs"
    assert finding.confidence is Confidence.HIGH


def test_rc005_dirty_working_tree() -> None:
    finding = next(
        item
        for item in run_checks(
            _facts(git=GitInfo(is_repository=True, is_clean=False, dirty_entries=2))
        )
        if item.id == "RC005"
    )
    assert finding.severity is Severity.INFO
    assert "2 changed" in finding.message


def test_rc006_rc007_rc008() -> None:
    files = [DetectedFile("README.md", "file", "docs")]
    missing = _basic_ids(detected_files=files, gitignore=GitignoreInfo(exists=False))
    assert {"RC003", "RC006", "RC007", "RC008"} <= missing


def test_rc009_counts_installers_and_lockfiles_only() -> None:
    signals = [
        PackageManagerSignal(
            "setuptools", "build-backend", "build-backend = setuptools.build_meta"
        ),
        PackageManagerSignal("uv", ROLE_LOCKFILE, "uv.lock"),
        PackageManagerSignal("pip", "dependency-declaration", "requirements.txt"),
    ]
    assert "RC009" not in _basic_ids(package_manager_signals=signals)


def test_rc009_fires_for_two_active_installers() -> None:
    signals = [
        PackageManagerSignal("uv", ROLE_INSTALLER, "uv.lock", SOURCE_CONFIG),
        PackageManagerSignal("poetry", ROLE_INSTALLER, "poetry.lock", SOURCE_CONFIG),
    ]
    assert "RC009" in _basic_ids(package_manager_signals=signals)


def test_rc010_no_python_files() -> None:
    project = ProjectScan(name="x", path="C:/x", python_file_count=0)
    assert "RC010" in _basic_ids(project=project)


def test_finding_serialisation_shape() -> None:
    finding = next(
        item
        for item in run_checks(_facts(git=GitInfo(is_repository=False)))
        if item.id == "RC004"
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
        "confidence",
    }
    assert finding.to_dict()["confidence"] == "high"


def test_ids_helper_is_usable() -> None:
    assert _ids() >= {"RC100"}
