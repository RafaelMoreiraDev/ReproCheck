"""Tests for package manager hint collection."""

from __future__ import annotations

from reprocheck.scanners import packaging


def _hints(root) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for hint in packaging.scan_package_manager_hints(root):
        result.setdefault(hint.manager, set()).add(hint.evidence)
    return result


def test_pip_and_setuptools_from_files(make_project) -> None:
    root = make_project({"requirements.txt": "", "setup.py": ""})
    hints = _hints(root)

    assert hints["pip"] == {"requirements.txt"}
    assert hints["setuptools"] == {"setup.py"}


def test_poetry_and_uv(make_project) -> None:
    root = make_project({"poetry.lock": "", "uv.lock": ""})
    hints = _hints(root)

    assert hints["poetry"] == {"poetry.lock"}
    assert hints["uv"] == {"uv.lock"}


def test_pipenv_from_pipfile(make_project) -> None:
    assert _hints(make_project({"Pipfile": ""}))["pipenv"] == {"Pipfile"}


def test_hints_from_pyproject_tool_sections(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": (
                "[build-system]\n"
                'requires = ["poetry-core"]\n'
                'build-backend = "poetry.core.masonry.api"\n'
                "\n[tool.uv]\ndev-dependencies = []\n"
            )
        }
    )
    hints = _hints(root)

    assert "poetry-core" in " ".join(hints["poetry"])
    assert "poetry.core.masonry.api" in " ".join(hints["poetry"])
    assert any("tool.uv" in evidence for evidence in hints["uv"])


def test_unrelated_build_requirements_are_not_managers(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": (
                "[build-system]\n"
                'requires = ["setuptools>=67", "wheel"]\n'
                'build-backend = "setuptools.build_meta"\n'
            )
        }
    )
    assert set(_hints(root)) == {"setuptools"}


def test_multiple_managers_are_all_reported(make_project) -> None:
    root = make_project({"requirements.txt": "", "poetry.lock": "", "uv.lock": ""})
    assert set(_hints(root)) == {"pip", "poetry", "uv"}


def test_no_signals(make_project) -> None:
    assert packaging.scan_package_manager_hints(make_project({"a.py": ""})) == []
