"""Tests for Python version requirement collection."""

from __future__ import annotations

from reprocheck.scanners import python_env


def test_python_version_file(make_project) -> None:
    root = make_project({".python-version": "# comment\n3.11.4\n"})
    found = python_env.scan_python_requirements(root)

    assert [(item.source, item.value) for item in found] == [
        (".python-version", "3.11.4")
    ]
    assert found[0].line == 2


def test_pyproject_requires_python(make_project) -> None:
    root = make_project(
        {"pyproject.toml": "[project]\nname = 'x'\nrequires-python = '>=3.11'\n"}
    )
    found = python_env.scan_python_requirements(root)

    assert len(found) == 1
    assert found[0].value == ">=3.11"
    assert found[0].source == "pyproject.toml [project.requires-python]"


def test_poetry_python_constraint(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": (
                "[tool.poetry]\nname = 'x'\n\n"
                "[tool.poetry.dependencies]\npython = '^3.10'\n"
            )
        }
    )
    found = python_env.scan_python_requirements(root)
    assert [item.value for item in found] == ["^3.10"]


def test_conflicting_sources_are_kept_separate(make_project) -> None:
    root = make_project(
        {
            ".python-version": "3.11\n",
            "pyproject.toml": "[project]\nrequires-python = '>=3.12'\n",
        }
    )
    values = sorted(item.value for item in python_env.scan_python_requirements(root))
    assert values == ["3.11", ">=3.12"]


def test_workflow_python_version(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n"
                "  test:\n"
                "    steps:\n"
                "      - uses: actions/setup-python@v5\n"
                "        with:\n"
                '          python-version: "3.12"  # pinned\n'
            )
        }
    )
    found = python_env.scan_python_requirements(root)

    assert len(found) == 1
    assert found[0].value == "3.12"
    assert found[0].file == ".github/workflows/ci.yml"
    assert found[0].line == 6


def test_workflow_matrix_is_expanded(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n"
                "  test:\n"
                "    strategy:\n"
                "      matrix:\n"
                "        python-version: ['3.10', '3.11']\n"
                "    steps:\n"
                "      - uses: actions/setup-python@v5\n"
                "        with:\n"
                "          python-version: ${{ matrix.python-version }}\n"
            )
        }
    )
    found = python_env.scan_python_requirements(root)
    assert sorted(item.value for item in found) == ["3.10", "3.11"]


def test_workflow_stringified_list_input(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/pytest.yaml": (
                "jobs:\n"
                "  test:\n"
                "    uses: org/.github/.github/workflows/python-test.yml@main\n"
                "    with:\n"
                "      python-version: \"['3.11']\"\n"
            )
        }
    )
    found = python_env.scan_python_requirements(root)
    assert [item.value for item in found] == ["3.11"]
    assert found[0].line == 5


def test_workflow_matrix_definition_is_not_duplicated(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n"
                "  test:\n"
                "    strategy:\n"
                "      matrix:\n"
                "        python-version: ['3.10', '3.11']\n"
                "    steps:\n"
                "      - uses: actions/setup-python@v5\n"
                "        with:\n"
                "          python-version: ${{ matrix.python-version }}\n"
            )
        }
    )
    found = python_env.scan_python_requirements(root)
    assert [(item.source, item.value) for item in found] == [
        (".github/workflows/ci.yml [matrix.python-version]", "3.10"),
        (".github/workflows/ci.yml [matrix.python-version]", "3.11"),
    ]


def test_unparseable_pyproject_falls_back_to_regex(make_project) -> None:
    root = make_project({"pyproject.toml": "[project\nrequires-python = '>=3.9'\n"})
    found = python_env.scan_python_requirements(root)
    assert [item.value for item in found] == [">=3.9"]


def test_no_requirements(make_project) -> None:
    assert python_env.scan_python_requirements(make_project({"a.py": ""})) == []
