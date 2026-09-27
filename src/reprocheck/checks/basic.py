"""Basic objective checks carried over from V0.1 (RC001-RC010)."""

from __future__ import annotations

from reprocheck.facts import ROLE_INSTALLER, ROLE_LOCKFILE, Facts
from reprocheck.models import Confidence, Finding, Severity

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


def check_readme(facts: Facts) -> Finding | None:
    if any(item.category == "docs" for item in facts.detected_files):
        return None
    return Finding(
        id=RC001_NO_README,
        title="No README detected",
        severity=Severity.WARNING,
        category="documentation",
        message="Neither README.md nor README.rst was found in the project root.",
        evidence="README.md, README.rst",
        confidence=Confidence.HIGH,
    )


def check_python_version(facts: Facts) -> Finding | None:
    if facts.python_requirements:
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
        confidence=Confidence.HIGH,
    )


def check_python_config(facts: Facts) -> Finding | None:
    if any(item.category in _PYTHON_CONFIG_CATEGORIES for item in facts.detected_files):
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
        confidence=Confidence.HIGH,
    )


def check_git(facts: Facts) -> Finding | None:
    if not facts.git.is_repository:
        return Finding(
            id=RC004_NOT_A_GIT_REPOSITORY,
            title="Directory is not a Git repository",
            severity=Severity.WARNING,
            category="vcs",
            message=(
                "The scanned directory is not the root of a Git repository, "
                "so no commit can be pinned."
            ),
            evidence=facts.git.error,
            confidence=Confidence.HIGH,
        )
    if facts.git.is_clean is False:
        return Finding(
            id=RC005_DIRTY_WORKING_TREE,
            title="Working tree has uncommitted changes",
            severity=Severity.INFO,
            category="vcs",
            message=(
                f"git status reported {facts.git.dirty_entries} changed path(s); "
                "the current state is not fully described by a commit."
            ),
            evidence="git status --porcelain",
            confidence=Confidence.HIGH,
        )
    return None


def check_tests(facts: Facts) -> Finding | None:
    if any(item.category == "tests" for item in facts.detected_files):
        return None
    return Finding(
        id=RC006_NO_TESTS,
        title="No tests directory detected",
        severity=Severity.INFO,
        category="tests",
        message="No tests/ directory was found in the project root.",
        evidence="tests/",
        confidence=Confidence.HIGH,
    )


def check_workflows(facts: Facts) -> Finding | None:
    if any(item.category == "ci" for item in facts.detected_files):
        return None
    return Finding(
        id=RC007_NO_CI_WORKFLOWS,
        title="No GitHub Actions workflows detected",
        severity=Severity.INFO,
        category="ci",
        message="No .yml/.yaml file was found in .github/workflows/.",
        evidence=".github/workflows/*.yml",
        confidence=Confidence.HIGH,
    )


def check_gitignore(facts: Facts) -> Finding | None:
    if facts.gitignore.exists:
        return None
    return Finding(
        id=RC008_NO_GITIGNORE,
        title="No .gitignore detected",
        severity=Severity.INFO,
        category="vcs",
        message="No .gitignore file was found in the project root.",
        evidence=".gitignore",
        confidence=Confidence.HIGH,
    )


def check_package_managers(facts: Facts) -> Finding | None:
    """Report several tools that manage the environment, not several signals.

    Build backends (setuptools) and plain dependency declarations (pip) are
    excluded: combining them with an installer is a legitimate setup.
    """
    managers = sorted(
        {
            signal.manager
            for signal in facts.package_manager_signals
            if signal.role in {ROLE_INSTALLER, ROLE_LOCKFILE}
        }
    )
    if len(managers) < 2:
        return None
    return Finding(
        id=RC009_MULTIPLE_PACKAGE_MANAGERS,
        title="Multiple active dependency-management signals",
        severity=Severity.INFO,
        category="packaging",
        message=(
            "Signals for more than one environment/lock tool were found: "
            + ", ".join(managers)
            + ". ReproCheck does not choose one."
        ),
        evidence="; ".join(
            f"{signal.manager}: {signal.evidence}"
            for signal in facts.package_manager_signals
            if signal.role in {ROLE_INSTALLER, ROLE_LOCKFILE}
        ),
        confidence=Confidence.HIGH,
    )


def check_python_files(facts: Facts) -> Finding | None:
    if facts.project.python_file_count > 0:
        return None
    return Finding(
        id=RC010_NO_PYTHON_FILES,
        title="No Python source files detected",
        severity=Severity.INFO,
        category="python",
        message="No .py file was found in the scanned directory tree.",
        evidence="**/*.py",
        confidence=Confidence.HIGH,
    )
