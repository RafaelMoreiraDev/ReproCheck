"""Tests for the dependency scanner (FACT layer)."""

from __future__ import annotations

from reprocheck.scanner import scan
from reprocheck.scanners import dependencies


def _scan(root):
    return dependencies.scan_dependencies(root)


def _by_name(result, name: str) -> list:
    return [item for item in result.declarations if item.name == name]


# --------------------------------------------------------------------------- #
# pyproject sources
# --------------------------------------------------------------------------- #


def test_pyproject_runtime_optional_and_groups(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": (
                "[project]\n"
                "name = 'x'\n"
                "dependencies = ['requests>=2.31', 'pydantic==2.6.2']\n"
                "[project.optional-dependencies]\n"
                "plot = ['matplotlib>=3.7']\n"
                "[dependency-groups]\n"
                "dev = ['pytest>=8']\n"
                "tests = ['coverage[toml]>=7']\n"
                "[build-system]\n"
                "requires = ['setuptools>=67']\n"
            )
        }
    )
    result = _scan(root)

    assert (result.summary["runtime"], result.summary["optional"]) == (2, 1)
    assert (result.summary["dev"], result.summary["test"]) == (1, 1)
    assert result.summary["build"] == 1
    assert result.summary["unique_packages"] == 6

    runtime = _by_name(result, "requests")[0]
    assert runtime.specifier == ">=2.31"
    assert runtime.kind == "runtime"
    assert runtime.group == "runtime"
    assert runtime.file == "pyproject.toml"
    assert runtime.raw == "requests>=2.31"

    optional = _by_name(result, "matplotlib")[0]
    assert optional.kind == "optional"
    assert optional.group == "plot"

    assert _by_name(result, "coverage")[0].kind == "test"
    assert _by_name(result, "setuptools")[0].kind == "build"


def test_poetry_dependencies(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": (
                "[tool.poetry]\n"
                "name = 'x'\n"
                "[tool.poetry.dependencies]\n"
                "python = '^3.11'\n"
                "requests = '^2.31'\n"
                "pydantic = { version = '2.6.2', extras = ['email'] }\n"
                "[tool.poetry.group.dev.dependencies]\n"
                "pytest = '^8.0'\n"
            )
        }
    )
    result = _scan(root)

    assert [item.name for item in result.declarations if item.name == "requests"]
    assert _by_name(result, "requests")[0].kind == "runtime"
    assert _by_name(result, "pydantic")[0].extras == ("email",)
    assert _by_name(result, "pytest")[0].kind == "dev"
    # ``python = '^3.11'`` is a interpreter constraint, not a dependency.
    assert not [item for item in result.declarations if item.raw_name == "python"]


# --------------------------------------------------------------------------- #
# requirements files
# --------------------------------------------------------------------------- #


def test_requirements_forms(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": (
                "# comment\n"
                "\n"
                "plain\n"
                "pinned==1.2.3\n"
                "ranged>=1,<2\n"
                "extras[foo]>=1\n"
                "marker>=1; python_version < '3.12'  # inline comment\n"
            )
        }
    )
    result = _scan(root)
    names = {item.name for item in result.declarations}

    assert names == {"plain", "pinned", "ranged", "extras", "marker"}
    assert _by_name(result, "pinned")[0].specifier == "==1.2.3"
    assert _by_name(result, "ranged")[0].specifier in {">=1,<2", "<2,>=1"}
    assert _by_name(result, "extras")[0].extras == ("foo",)
    marker = _by_name(result, "marker")[0]
    assert marker.marker == 'python_version < "3.12"'
    assert marker.is_conditional is True
    assert _by_name(result, "plain")[0].line == 3


def test_requirements_grouping(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": "requests\n",
            "requirements-dev.txt": "pytest\n",
            "requirements-test.txt": "coverage\n",
            "requirements/docs.txt": "sphinx\n",
        }
    )
    result = _scan(root)
    kinds = {item.name: item.kind for item in result.declarations}

    assert kinds == {
        "requests": "runtime",
        "pytest": "dev",
        "coverage": "test",
        "sphinx": "dev",
    }


def test_constraint_file_declarations(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": "-c constraints.txt\nrequests\n",
            "constraints.txt": "requests==2.31.0\n",
        }
    )
    result = _scan(root)
    constraint = _by_name(result, "requests")

    assert {item.kind for item in constraint} == {"runtime", "constraint"}
    constraint_declaration = next(
        item for item in constraint if item.kind == "constraint"
    )
    assert constraint_declaration.source == "constraint"


# --------------------------------------------------------------------------- #
# includes
# --------------------------------------------------------------------------- #


def test_existing_include_is_followed(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": "-r requirements/base.txt\nrequests\n",
            "requirements/base.txt": "numpy==1.23.5\n",
        }
    )
    result = _scan(root)

    assert _by_name(result, "numpy")[0].file == "requirements/base.txt"
    assert result.includes[0].exists is True
    assert result.includes[0].resolved == "requirements/base.txt"
    assert result.includes[0].include_kind == "include"


def test_missing_include(make_project) -> None:
    root = make_project({"requirements.txt": "-r requirements/base.txt\n"})
    result = _scan(root)

    assert result.includes[0].exists is False
    assert result.includes[0].skipped_reason == "missing"


def test_constraint_include_is_recorded(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": "-c constraints.txt\n",
            "constraints.txt": "requests==2.31.0\n",
        }
    )
    result = _scan(root)

    assert [item.include_kind for item in result.includes] == ["constraint"]
    assert result.includes[0].exists is True


def test_include_cycle_terminates(make_project) -> None:
    root = make_project(
        {
            "a.txt": "-r b.txt\naaa\n",
            "b.txt": "-r a.txt\nbbb\n",
        }
    )
    result = dependencies.scan_dependencies(root)
    # ``a.txt`` is not a requirements file, so nothing is parsed from it.
    assert result.declarations == []


def test_include_cycle_between_requirements_files(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": "-r requirements-b.txt\naaa\n",
            "requirements-b.txt": "-r requirements.txt\nbbb\n",
        }
    )
    result = _scan(root)
    names = sorted(item.name for item in result.declarations)

    assert names == ["aaa", "bbb"]


def test_remote_include_is_not_followed(make_project) -> None:
    root = make_project(
        {"requirements.txt": "-r https://example.com/req.txt\nrequests\n"}
    )
    result = _scan(root)

    assert result.includes[0].skipped_reason == "remote-include"
    assert result.includes[0].exists is None
    assert _by_name(result, "requests")


def test_include_outside_the_project_is_skipped(make_project) -> None:
    root = make_project({"requirements.txt": "-r ../outside.txt\n"})
    result = _scan(root)

    assert result.includes[0].skipped_reason == "outside-project"
    assert result.includes[0].exists is None


# --------------------------------------------------------------------------- #
# non-registry dependencies
# --------------------------------------------------------------------------- #


def test_vcs_with_commit(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": "mypkg @ git+https://github.com/org/mypkg@0123456789abcdef0123456789abcdef01234567\n"
        }
    )
    declaration = _scan(root).declarations[0]

    assert declaration.reference_kind == "vcs"
    assert declaration.vcs_commit == "0123456789abcdef0123456789abcdef01234567"
    assert declaration.vcs_ref == "0123456789abcdef0123456789abcdef01234567"


def test_vcs_with_branch(make_project) -> None:
    root = make_project(
        {"requirements.txt": "mypkg @ git+https://github.com/org/mypkg@main\n"}
    )
    declaration = _scan(root).declarations[0]

    assert declaration.reference_kind == "vcs"
    assert declaration.vcs_ref == "main"
    assert declaration.vcs_commit is None


def test_vcs_without_ref(make_project) -> None:
    root = make_project(
        {"requirements.txt": "mypkg @ git+https://github.com/org/mypkg\n"}
    )
    declaration = _scan(root).declarations[0]

    assert declaration.reference_kind == "vcs"
    assert declaration.vcs_ref is None


def test_bare_vcs_url(make_project) -> None:
    root = make_project(
        {"requirements.txt": "git+https://github.com/org/mypkg@main#egg=mypkg\n"}
    )
    declaration = _scan(root).declarations[0]

    assert declaration.reference_kind == "vcs"
    assert declaration.name == "mypkg"


def test_direct_wheel_url(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": (
                "https://example.com/packages/numpy-1.23.5-cp311-cp311-win_amd64.whl\n"
            )
        }
    )
    declaration = _scan(root).declarations[0]

    assert declaration.reference_kind == "url"
    assert declaration.name == ""


def test_egg_fragment_keeps_the_url(make_project) -> None:
    root = make_project(
        {"requirements.txt": ("https://example.com/numpy.zip#egg=numpy\n")}
    )
    declaration = _scan(root).declarations[0]

    assert declaration.reference_kind == "url"
    assert "egg=numpy" in str(declaration.reference)


def test_editable_local_path(make_project) -> None:
    root = make_project(
        {"requirements.txt": "-e ./vendor/mypkg\nrequests\n", "vendor/mypkg/x.py": ""}
    )
    result = _scan(root)
    editable = [item for item in result.declarations if item.reference_kind]

    assert editable[0].reference == "./vendor/mypkg"
    assert editable[0].kind == "dev"
    assert _by_name(result, "requests")


# --------------------------------------------------------------------------- #
# name normalisation
# --------------------------------------------------------------------------- #


def test_name_normalisation_follows_python_standard_rules(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": "Foo_Bar==1.0\nfoo-bar==2.0\nfoo.bar\n",
            "pyproject.toml": "[project]\nname = 'x'\ndependencies = ['FOO__BAR']\n",
        }
    )
    result = _scan(root)
    names = {item.name for item in result.declarations}

    # PEP 503 normalisation treats ``.`` and ``_`` as ``-``.
    assert names == {"foo-bar"}


def test_unparsable_line_is_ignored(make_project) -> None:
    root = make_project({"requirements.txt": "this is not a requirement !!!\nok==1\n"})
    result = _scan(root)

    assert [item.name for item in result.declarations] == ["ok"]


def test_scan_report_contains_dependency_section(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": "[project]\nname = 'x'\ndependencies = ['requests==2.31']\n",
            "requirements-dev.txt": "pytest\n",
        }
    )
    report = scan(root)
    section = report.dependencies

    assert list(section) == ["declarations", "includes", "summary"]
    assert section["summary"]["runtime"] == 1  # type: ignore[index]
    assert section["summary"]["dev"] == 1  # type: ignore[index]
    assert section["declarations"][0]["name"] == "pytest"  # type: ignore[index]
