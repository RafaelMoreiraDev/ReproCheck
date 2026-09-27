"""Tests for the Python version reconciliation rules (RC100-RC104)."""

from __future__ import annotations

import pytest

from reprocheck.checks.python_versions import check_python_versions
from reprocheck.facts import Facts
from reprocheck.models import Confidence, ProjectScan, PythonRequirement, Severity
from reprocheck.scanner import scan

WORKFLOW = (
    "jobs:\n"
    "  test:\n"
    "    steps:\n"
    "      - uses: actions/setup-python@v5\n"
    "        with:\n"
    "          python-version: '{version}'\n"
)


def _facts(*requirements: tuple[str, str]) -> Facts:
    return Facts(
        project=ProjectScan(name="x", path="C:/tmp", python_file_count=1),
        python_requirements=[
            PythonRequirement(source, value, file=source)
            for source, value in requirements
        ],
    )


def _ids(facts: Facts) -> set[str]:
    return {finding.id for finding in check_python_versions(facts)}


def _finding(facts: Facts, identifier: str):
    return next(
        finding for finding in check_python_versions(facts) if finding.id == identifier
    )


# --------------------------------------------------------------------------- #
# 1. compatible declarations
# --------------------------------------------------------------------------- #


def test_3_11_against_at_least_3_11_is_consistent() -> None:
    facts = _facts((".python-version", "3.11"), ("pyproject.toml", ">=3.11"))
    assert _ids(facts) == {"RC100"}


def test_ci_pin_within_range_is_consistent() -> None:
    facts = _facts(
        ("pyproject.toml [project.requires-python]", ">=3.11"),
        (".github/workflows/ci.yml [python-version]", "3.12"),
    )
    assert _ids(facts) == {"RC100"}


# --------------------------------------------------------------------------- #
# 2. local version below the project requirement
# --------------------------------------------------------------------------- #


def test_local_3_10_against_at_least_3_11_conflicts() -> None:
    facts = _facts((".python-version", "3.10"), ("pyproject.toml", ">=3.11"))
    finding = _finding(facts, "RC101")

    assert _ids(facts) == {"RC101"}
    assert finding.severity is Severity.WARNING
    assert finding.confidence is Confidence.HIGH
    assert finding.file == ".python-version"
    assert "3.10" in finding.message
    assert ">=3.11" in finding.message


def test_local_3_11_against_upper_bound_conflicts() -> None:
    facts = _facts((".python-version", "3.12"), ("pyproject.toml", ">=3.11,<3.12"))
    assert "RC101" in _ids(facts)


# --------------------------------------------------------------------------- #
# 3. CI testing an unsupported version
# --------------------------------------------------------------------------- #


def test_ci_3_10_against_at_least_3_11(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": "[project]\nname = 'x'\nrequires-python = '>=3.11'\n",
            ".github/workflows/ci.yml": WORKFLOW.format(version="3.10"),
        }
    )
    findings = {item.id: item for item in scan(root).findings}

    assert "RC102" in findings
    assert findings["RC102"].severity is Severity.WARNING
    assert findings["RC102"].confidence is Confidence.HIGH
    assert "3.10" in findings["RC102"].message
    assert ">=3.11" in findings["RC102"].message
    assert "RC100" not in findings


def test_ci_matrix_containing_unsupported_version(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": "[project]\nname = 'x'\nrequires-python = '>=3.11'\n",
            ".github/workflows/ci.yml": (
                "jobs:\n"
                "  test:\n"
                "    strategy:\n"
                "      matrix:\n"
                "        python-version: ['3.10', '3.11', '3.12']\n"
                "    steps:\n"
                "      - uses: actions/setup-python@v5\n"
                "        with:\n"
                "          python-version: ${{ matrix.python-version }}\n"
            ),
        }
    )
    conflicts = [item for item in scan(root).findings if item.id == "RC102"]

    assert len(conflicts) == 1
    assert "3.10" in conflicts[0].message


# --------------------------------------------------------------------------- #
# RC103 - no overlapping version
# --------------------------------------------------------------------------- #


def test_empty_intersection_is_an_error() -> None:
    facts = _facts(
        ("pyproject.toml [project.requires-python]", ">=3.11,<3.12"),
        ("pyproject.toml [tool.poetry.dependencies.python]", ">=3.13"),
    )
    finding = _finding(facts, "RC103")

    assert finding.severity is Severity.ERROR
    assert finding.confidence is Confidence.HIGH
    assert "3.11" in str(finding.evidence)
    assert "3.13" in str(finding.evidence)


def test_upper_and_lower_bounds_that_cannot_meet() -> None:
    facts = _facts(
        ("pyproject.toml [project.requires-python]", ">=3.11"),
        ("pyproject.toml [tool.poetry.dependencies.python]", "<3.10"),
    )
    assert "RC103" in _ids(facts)


def test_overlapping_bounds_do_not_report_rc103() -> None:
    facts = _facts(
        ("pyproject.toml [project.requires-python]", ">=3.11"),
        ("pyproject.toml [tool.poetry.dependencies.python]", ">=3.9"),
    )
    assert "RC103" not in _ids(facts)


def test_local_pin_alone_does_not_report_rc103() -> None:
    facts = _facts((".python-version", "3.10"), ("pyproject.toml", ">=3.11"))
    assert "RC103" not in _ids(facts)


# --------------------------------------------------------------------------- #
# Specifier dialects and unparsable values
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "declared", ["^3.10", "~3.10", ">=3.10,<4.0", "==3.11.*", "3.11"]
)
def test_constraint_dialects_are_understood(declared: str) -> None:
    """Every dialect must be parsed, never reported as unparsable."""
    below = _facts(
        (".python-version", "3.9"),
        ("pyproject.toml [tool.poetry.dependencies.python]", declared),
    )
    assert "RC104" not in _ids(below)
    assert "RC101" in _ids(below)


def test_caret_allows_the_declared_minimum() -> None:
    facts = _facts(
        (".python-version", "3.10"),
        ("pyproject.toml [tool.poetry.dependencies.python]", "^3.10"),
    )
    assert "RC101" not in _ids(facts)
    assert "RC104" not in _ids(facts)


def test_conflicting_exact_python_pins_in_the_project(make_project) -> None:
    """Two exact project pins with no common version are a RC103 error."""
    root = make_project(
        {
            "pyproject.toml": (
                "[project]\n"
                "name = 'x'\n"
                "requires-python = '==3.11.4'\n"
                "[tool.poetry.dependencies]\n"
                "python = '3.12.1'\n"
            )
        }
    )
    findings = {item.id: item for item in scan(root).findings}

    assert findings["RC103"].severity is Severity.ERROR
    assert "==3.11.4" in findings["RC103"].message or "3.11.4" in str(
        findings["RC103"].evidence
    )
    assert "RC100" not in findings


def test_matching_exact_python_pins_are_consistent() -> None:
    facts = _facts(
        ("pyproject.toml [project.requires-python]", "3.11.4"),
        ("pyproject.toml [tool.poetry.dependencies.python]", "3.11.4"),
    )
    assert _ids(facts) == {"RC100"}


def test_ci_matrix_with_several_pins_is_not_a_conflict() -> None:
    facts = _facts(
        ("pyproject.toml [project.requires-python]", ">=3.10"),
        (".github/workflows/ci.yml [python-version]", "3.10"),
        (".github/workflows/ci.yml [python-version]", "3.12"),
    )
    assert "RC103" not in _ids(facts)


def test_unparsable_declaration_is_reported_once() -> None:
    facts = _facts(
        (".python-version", "3.11"),
        (".github/workflows/ci.yml [python-version]", "not-a-version"),
    )
    findings = check_python_versions(facts)

    assert [item.id for item in findings] == ["RC104"]
    assert findings[0].severity is Severity.INFO
    assert findings[0].confidence is Confidence.HIGH
    assert ".github/workflows/ci.yml" in str(findings[0].file)


def test_no_declarations_produce_nothing() -> None:
    assert _ids(Facts(project=ProjectScan(name="x", path="C:/x"))) == set()


def test_boundary_versions() -> None:
    facts = _facts(("pyproject.toml", ">=3.11,<3.11.1"))
    assert "RC100" in _ids(facts)
