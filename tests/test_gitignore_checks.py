"""Tests for the .gitignore artifact check (RC140)."""

from __future__ import annotations

from reprocheck.scanner import scan

COMPLETE = """\
__pycache__/
*.py[cod]
.venv/
venv/
build/
dist/
*.egg-info/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.tox/
.nox/
"""


def _findings(root, identifier: str = "RC140") -> list:
    return [item for item in scan(root).findings if item.id == identifier]


def _finding(root):
    findings = _findings(root)
    return findings[0] if findings else None


PYPROJECT = (
    "[project]\n"
    "name = 'x'\n"
    "[build-system]\n"
    "requires = ['setuptools>=67']\n"
    'build-backend = "setuptools.build_meta"\n'
    "[tool.ruff]\n"
    "line-length = 88\n"
    "[tool.mypy]\n"
    "strict = true\n"
)


# --------------------------------------------------------------------------- #
# 12. appropriate .gitignore
# --------------------------------------------------------------------------- #


def test_complete_gitignore_produces_no_finding(make_project) -> None:
    root = make_project(
        {
            ".gitignore": COMPLETE,
            "pyproject.toml": PYPROJECT,
            "a.py": "",
            "tests/test_a.py": "",
            ".python-version": "3.11\n",
        }
    )
    assert _findings(root) == []


def test_trailing_slash_is_optional_in_the_gitignore(make_project) -> None:
    root = make_project(
        {
            ".gitignore": "__pycache__\nvenv\n*.egg-info\n",
            "pyproject.toml": PYPROJECT,
            "a.py": "",
            "tests/test_a.py": "",
        }
    )
    finding = _finding(root)
    assert finding is not None
    assert "venv/" not in finding.message


# --------------------------------------------------------------------------- #
# 13. missing or incomplete .gitignore
# --------------------------------------------------------------------------- #


def test_missing_gitignore_is_reported_once(make_project) -> None:
    root = make_project({"pyproject.toml": PYPROJECT, "a.py": ""})
    ids = {item.id for item in scan(root).findings}

    assert "RC008" in ids
    # RC140 needs a .gitignore to inspect; RC008 already covers its absence.
    assert "RC140" not in ids


def test_incomplete_gitignore_lists_only_relevant_patterns(make_project) -> None:
    root = make_project(
        {
            ".gitignore": "__pycache__/\nbuild/\ndist/\n*.egg-info/\n",
            "pyproject.toml": PYPROJECT
            + "[tool.pytest.ini_options]\nminversion = '8'\n",
            "a.py": "",
            "tests/test_a.py": "",
            ".python-version": "3.11\n",
        }
    )
    finding = _finding(root)

    assert finding is not None
    assert finding.severity.name == "WARNING"
    assert finding.confidence.name == "HIGH"
    message = finding.message
    assert ".venv/" in message
    assert ".pytest_cache/" in message
    assert ".ruff_cache/" in message
    assert ".mypy_cache/" in message
    assert "__pycache__/" not in message
    assert "build/" not in message


def test_patterns_of_unused_tools_are_not_required(make_project) -> None:
    root = make_project(
        {
            ".gitignore": "__pycache__/\n.venv/\nvenv/\nbuild/\ndist/\n*.egg-info/\n",
            "a.py": "",
        }
    )
    assert _finding(root) is None


def test_tests_directory_alone_does_not_imply_pytest(make_project) -> None:
    root = make_project(
        {
            ".gitignore": "__pycache__/\n",
            "pyproject.toml": "[project]\nname = 'x'\n",
            "a.py": "",
            "tests/test_a.py": "",
        }
    )
    finding = _finding(root)
    assert finding is not None
    assert "build/" in finding.message
    assert ".pytest_cache/" not in finding.message


def test_venv_patterns_require_evidence(make_project) -> None:
    root = make_project(
        {
            ".gitignore": "__pycache__/\n",
            "a.py": "",
            "README.md": "# x\n\n```bash\npython3 -m venv venv\n```\n",
        }
    )
    finding = _finding(root)
    assert finding is not None
    assert ".venv/" in finding.message


def test_ruff_cache_requires_ruff_evidence(make_project) -> None:
    root = make_project(
        {
            ".gitignore": "__pycache__/\n.pytest_cache/\n",
            "a.py": "",
            "tests/test_a.py": "",
            "pyproject.toml": "[project]\nname = 'x'\n[tool.ruff]\nline-length = 88\n",
        }
    )
    finding = _finding(root)
    assert finding is not None
    assert ".ruff_cache/" in finding.message
    assert ".mypy_cache/" not in finding.message


def test_gitignore_facts_are_exposed(make_project) -> None:
    root = make_project({".gitignore": "# comment\n\n__pycache__/\n!keep.py\n"})
    gitignore = scan(root).facts["gitignore"]

    assert gitignore == {
        "exists": True,
        "file": ".gitignore",
        "patterns": ["__pycache__"],
    }
