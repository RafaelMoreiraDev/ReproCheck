"""Objective checks that turn scan data into findings.

Every check in V0.1 states a fact that can be verified from the scan data
alone. No check infers, guesses or judges code quality.
"""

from __future__ import annotations

from reprocheck.models import (
    DetectedFile,
    Finding,
    GitInfo,
    PackageManagerHint,
    ProjectScan,
    PythonRequirement,
    Severity,
)

RC001_NO_README = "RC001"
RC002_NO_PYTHON_VERSION = "RC002"
RC003_NO_PYTHON_CONFIG = "RC003"
RC004_NOT_A_GIT_REPOSITORY = "RC004"
RC005_DIRTY_WORKING_TREE = "RC005"
RC006_NO_TESTS = "RC006"
RC007_NO_CI_WORKFLOWS = "RC007"
RC008_NO_GITIGNORE = "RC008"
RC009_MULTIPLE_PACKAGE_MANAGERS = "RC009"
RC010_NO_PYTHON_FILES = "RC010"

_PYTHON_CONFIG_CATEGORIES = frozenset({"python-config"})


def run_checks(
    project: ProjectScan,
    git: GitInfo,
    detected_files: list[DetectedFile],
    python_requirements: list[PythonRequirement],
    package_manager_hints: list[PackageManagerHint],
) -> list[Finding]:
    """Return every finding for a completed scan."""
    findings: list[Finding] = [
        _check_readme(detected_files),
        _check_python_version(python_requirements),
        _check_python_config(detected_files),
        _check_git(git),
        _check_tests(detected_files),
        _check_workflows(detected_files),
        _check_gitignore(detected_files),
        _check_package_managers(package_manager_hints),
        _check_python_files(project),
    ]
    return [finding for finding in findings if finding is not None]


def _check_readme(files: list[DetectedFile]) -> Finding | None:
    if any(item.category == "docs" for item in files):
        return None
    return Finding(
        id=RC001_NO_README,
        title="No README detected",
        severity=Severity.WARNING,
        category="documentation",
        message="Neither README.md nor README.rst was found in the project root.",
        evidence="README.md, README.rst",
    )


def _check_python_version(requirements: list[PythonRequirement]) -> Finding | None:
    if requirements:
        return None
    return Finding(
        id=RC002_NO_PYTHON_VERSION,
        title="No Python version requirement detected",
        severity=Severity.WARNING,
        category="python",
        message=(
            "No Python version requirement was found in .python-version, "
            "pyproject.toml or GitHub Actions workflows."
        ),
        evidence=".python-version, pyproject.toml, .github/workflows/*",
    )


def _check_python_config(files: list[DetectedFile]) -> Finding | None:
    if any(item.category in _PYTHON_CONFIG_CATEGORIES for item in files):
        return None
    return Finding(
        id=RC003_NO_PYTHON_CONFIG,
        title="No Python dependency or configuration file detected",
        severity=Severity.WARNING,
        category="python",
        message=(
            "No apparent Python dependency or configuration file was found "
            "(pyproject.toml, requirements*.txt, setup.py, setup.cfg, "
            "Pipfile, poetry.lock, uv.lock)."
        ),
        evidence="pyproject.toml, requirements.txt, setup.py, Pipfile",
    )


def _check_git(git: GitInfo) -> Finding | None:
    if not git.is_repository:
        return Finding(
            id=RC004_NOT_A_GIT_REPOSITORY,
            title="Directory is not a Git repository",
            severity=Severity.WARNING,
            category="vcs",
            message=(
                "The scanned directory is not the root of a Git repository, "
                "so no commit can be pinned."
            ),
            evidence=git.error,
        )
    if git.is_clean is False:
        return Finding(
            id=RC005_DIRTY_WORKING_TREE,
            title="Working tree has uncommitted changes",
            severity=Severity.INFO,
            category="vcs",
            message=(
                f"git status reported {git.dirty_entries} changed path(s); the "
                "current state is not fully described by a commit."
            ),
            evidence="git status --porcelain",
        )
        return None


def _check_tests(files: list[DetectedFile]) -> Finding | None:
    if any(item.category == "tests" for item in files):
        return None
    return Finding(
        id=RC006_NO_TESTS,
        title="No tests directory detected",
        severity=Severity.INFO,
        category="tests",
        message="No tests/ directory was found in the project root.",
        evidence="tests/",
    )


def _check_workflows(files: list[DetectedFile]) -> Finding | None:
    if any(item.category == "ci" for item in files):
        return None
    return Finding(
        id=RC007_NO_CI_WORKFLOWS,
        title="No GitHub Actions workflows detected",
        severity=Severity.INFO,
        category="ci",
        message="No .yml/.yaml file was found in .github/workflows/.",
        evidence=".github/workflows/*.yml",
    )


def _check_gitignore(files: list[DetectedFile]) -> Finding | None:
    if any(item.path == ".gitignore" for item in files):
        return None
    return Finding(
        id=RC008_NO_GITIGNORE,
        title="No .gitignore detected",
        severity=Severity.INFO,
        category="vcs",
        message="No .gitignore file was found in the project root.",
        evidence=".gitignore",
    )


def _check_package_managers(hints: list[PackageManagerHint]) -> Finding | None:
    managers = sorted({hint.manager for hint in hints})
    if len(managers) < 2:
        return None
    return Finding(
        id=RC009_MULTIPLE_PACKAGE_MANAGERS,
        title="Multiple package manager signals",
        severity=Severity.INFO,
        category="packaging",
        message=(
            "Signals for more than one package manager were found: "
            + ", ".join(managers)
            + ". ReproCheck does not choose one."
        ),
        evidence="; ".join(f"{hint.manager}: {hint.evidence}" for hint in hints),
    )


def _check_python_files(project: ProjectScan) -> Finding | None:
    if project.python_file_count > 0:
        return None
    return Finding(
        id=RC010_NO_PYTHON_FILES,
        title="No Python source files detected",
        severity=Severity.INFO,
        category="python",
        message="No .py file was found in the scanned directory tree.",
        evidence="**/*.py",
    )
