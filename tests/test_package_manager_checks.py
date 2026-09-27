"""Tests for the package manager reconciliation rules (RC110-RC116)."""

from __future__ import annotations

from reprocheck.checks.package_managers import check_package_manager_roles
from reprocheck.facts import Facts
from reprocheck.models import Confidence, ProjectScan, Severity
from reprocheck.scanner import scan

PYPROJECT_UV = "[project]\nname = 'x'\n[tool.uv]\ndev-dependencies = []\n"
PYPROJECT_PIP = (
    "[project]\nname = 'x'\ndependencies = ['requests==2.0']\n"
    "[build-system]\nrequires = ['setuptools>=67']\n"
    'build-backend = "setuptools.build_meta"\n'
)
README_PIP = "# x\n\n```bash\npip install -e .\npytest\n```\n"
README_POETRY = "# x\n\n```bash\npoetry install\npoetry run pytest\n```\n"


def _ids(root) -> set[str]:
    return {finding.id for finding in scan(root).findings}


def _finding(root, identifier: str):
    return next(finding for finding in scan(root).findings if finding.id == identifier)


def _unit_ids(facts: Facts) -> set[str]:
    return {finding.id for finding in check_package_manager_roles(facts)}


# --------------------------------------------------------------------------- #
# 4. legitimate combinations must not be reported as conflicts
# --------------------------------------------------------------------------- #


def test_setuptools_with_uv_is_not_a_conflict(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": PYPROJECT_UV,
            "uv.lock": "",
            "README.md": README_PIP,
        }
    )
    ids = _ids(root)
    assert "RC009" not in ids
    assert "RC110" not in ids
    assert "RC111" not in ids
    assert "RC112" not in ids
    assert "RC113" not in ids


def test_setuptools_with_pip_is_not_a_conflict(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": PYPROJECT_PIP,
            "requirements.txt": "requests==2.0\n",
            "setup.py": "",
            "README.md": README_PIP,
        }
    )
    ids = _ids(root)
    assert not {"RC009", "RC110", "RC111", "RC112"} & ids


def test_build_backend_alone_produces_nothing() -> None:
    facts = Facts(project=ProjectScan(name="x", path="C:/x"))
    assert _unit_ids(facts) == set()


# --------------------------------------------------------------------------- #
# 5. several lockfiles
# --------------------------------------------------------------------------- #


def test_poetry_lock_with_uv_lock_is_a_multiple_lockfile_signal(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": PYPROJECT_UV + "\n[tool.poetry]\nname = 'x'\n",
            "poetry.lock": "",
            "uv.lock": "",
        }
    )
    findings = {item.id: item for item in scan(root).findings}

    assert "RC110" in findings
    assert findings["RC110"].severity is Severity.WARNING
    assert findings["RC110"].confidence is Confidence.MEDIUM
    assert "poetry" in findings["RC110"].message
    assert "uv" in findings["RC110"].message
    # Both tools are configured, so no stale-lockfile finding.
    assert "RC111" not in findings
    assert "RC116" not in findings


def test_pipfile_lock_with_poetry_lock(make_project) -> None:
    root = make_project(
        {
            "Pipfile": "",
            "Pipfile.lock": "",
            "poetry.lock": "",
            "pyproject.toml": "[tool.poetry]\nname = 'x'\n",
        }
    )
    assert "RC110" in _ids(root)


# --------------------------------------------------------------------------- #
# Lockfile without configuration
# --------------------------------------------------------------------------- #


def test_uv_lock_without_uv_configuration(make_project) -> None:
    root = make_project({"pyproject.toml": "[project]\nname = 'x'\n", "uv.lock": ""})
    finding = _finding(root, "RC111")

    assert finding.severity is Severity.WARNING
    assert finding.confidence is Confidence.MEDIUM
    assert "uv.lock" in finding.evidence


def test_uv_lock_documented_in_readme_is_accepted(make_project) -> None:
    root = make_project(
        {
            "uv.lock": "",
            "README.md": "# x\n\n```bash\nuv sync\n```\n",
        }
    )
    assert "RC111" not in _ids(root)


def test_configured_uv_without_lockfile(make_project) -> None:
    root = make_project({"pyproject.toml": PYPROJECT_UV})
    finding = _finding(root, "RC116")

    assert finding.severity is Severity.WARNING
    assert finding.confidence is Confidence.HIGH
    assert "uv.lock" in finding.message


def test_orphan_pipfile_lock(make_project) -> None:
    root = make_project({"Pipfile.lock": ""})
    finding = _finding(root, "RC113")

    assert finding.severity is Severity.WARNING
    assert finding.confidence is Confidence.HIGH
    assert "Pipfile" in finding.message


# --------------------------------------------------------------------------- #
# Documentation versus configuration
# --------------------------------------------------------------------------- #


def test_documented_poetry_without_configuration(make_project) -> None:
    root = make_project({"README.md": README_POETRY})
    finding = _finding(root, "RC112")

    assert finding.severity is Severity.WARNING
    assert finding.confidence is Confidence.MEDIUM
    assert "poetry" in finding.message


def test_documented_install_ignores_foreign_lockfile(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": PYPROJECT_UV,
            "uv.lock": "",
            "README.md": README_PIP,
        }
    )
    finding = _finding(root, "RC114")

    assert finding.severity is Severity.INFO
    assert finding.confidence is Confidence.MEDIUM
    assert "uv" in finding.message


def test_documented_install_using_the_lockfile_tool_is_accepted(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": PYPROJECT_UV,
            "uv.lock": "",
            "README.md": "# x\n\n```bash\nuv sync\nuv run pytest\n```\n",
        }
    )
    assert "RC114" not in _ids(root)


def test_documented_install_without_any_lockfile(make_project) -> None:
    root = make_project({"pyproject.toml": PYPROJECT_PIP, "README.md": README_PIP})
    assert "RC114" not in _ids(root)


def test_documented_package_missing_from_metadata(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": (
                "[project]\nname = 'x'\ndependencies = ['requests==2.0']\n"
            ),
            "README.md": (
                "# x\n\n```bash\n"
                "pip install -e .\n"
                "conda install -c conda-forge pyresample\n"
                "```\n"
            ),
        }
    )
    finding = _finding(root, "RC115")

    assert finding.severity is Severity.WARNING
    assert "pyresample" in finding.message
    # The channel name is an option value, not a distribution.
    assert "conda-forge" not in finding.message


def test_declared_packages_are_not_reported(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": (
                "[project]\nname = 'x'\n"
                "dependencies = ['requests==2.0', 'pyresample>=2023']\n"
            ),
            "README.md": "# x\n\n```bash\npip install -e .\n```\n",
        }
    )
    assert "RC115" not in _ids(root)
