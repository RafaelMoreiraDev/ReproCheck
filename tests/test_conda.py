"""Tests for the Conda environment source.

The suite is organised by the question each test answers, not by the code path
it happens to cover:

* **reading** - what is parsed out of a file, and what is refused;
* **translating** - when Conda syntax may be turned into PEP 440, and when it
  must not;
* **reconciling** - when a disagreement is proven, and when only observed;
* **reporting** - JSON, Markdown, terminal, diff;
* **refusing** - that nothing here writes, patches, executes or reaches the
  network.

Every test that runs a command asserts the analysed project is byte for byte
unchanged, which is the property that matters most for a tool whose whole
promise is to be read-only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from conftest import snapshot
from reprocheck.checks.conda import (
    RC230_CONDA_PYTHON_CONFLICT,
    RC231_CONDA_DEPENDENCY_CONFLICT,
    RC232_CONDA_DEPENDENCY_DIFFERS,
    RC233_CONDA_PIP_DUPLICATE,
    RC234_CONDA_UNREADABLE,
    RC235_CONDA_PIP_NOT_DECLARED,
    check_conda,
    widen_wildcard_pin,
)
from reprocheck.facts import (
    CONDA_SOURCE_DEPENDENCY,
    CONDA_SOURCE_PIP,
    CondaDependency,
    CondaEnvironment,
    Facts,
)
from reprocheck.models import Confidence, Finding, Severity
from reprocheck.models.detected import PythonRequirement
from reprocheck.models.project import ProjectScan
from reprocheck.scanner import scan
from reprocheck.scanners.conda import (
    conda_to_pep440,
    explicitly_named_environment_files,
    parse_environment,
    parse_pip_requirement,
    scan_conda,
    selectors_by_line,
)

BASIC = """
name: demo
channels:
  - conda-forge
  - defaults
dependencies:
  - python=3.11
  - numpy>=1.26
  - scipy
  - pip
  - pip:
      - requests>=2
      - package[extra]==1.2
"""

PYPROJECT = """[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "demo"
version = "0.1.0"
requires-python = "{requires}"
{dependencies}
"""


def project(
    make_project,
    *,
    environment: str | None = BASIC,
    requires: str = ">=3.11",
    dependencies: str = "",
    extra_files: dict[str, str] | None = None,
    name: str = "example",
):
    """A project with a pyproject and, by default, one Conda environment."""
    files = {
        "pyproject.toml": PYPROJECT.format(requires=requires, dependencies=dependencies)
    }
    if environment is not None:
        files["environment.yml"] = environment
    files.update(extra_files or {})
    return make_project(files, name=name)


def environment_of(root: Path) -> CondaEnvironment:
    return parse_environment(root / "environment.yml", root)


def environment_from(make_project, text: str, **kwargs) -> CondaEnvironment:
    return environment_of(project(make_project, environment=text, **kwargs))


def findings_of(root: Path, prefix: str = "RC23") -> list[Finding]:
    report = scan(root)
    return [item for item in report.findings if item.id.startswith(prefix)]


def ids_of(root: Path, prefix: str = "RC23") -> set[str]:
    return {item.id for item in findings_of(root, prefix)}


# --------------------------------------------------------------------------- #
# 1. a basic environment.yml
# --------------------------------------------------------------------------- #


def test_basic_environment_is_read(make_project) -> None:
    environment = environment_from(make_project, BASIC)

    assert environment.name == "demo"
    assert environment.channels == ("conda-forge", "defaults")
    assert environment.parse_error is None
    assert environment.unsupported == ()
    names = [item.name for item in environment.conda_dependencies]
    assert names == ["python", "numpy", "scipy", "pip"]


def test_environment_yaml_is_read_too(make_project) -> None:
    root = make_project({"environment.yaml": BASIC}, name="yaml")
    report = scan(root)

    assert report.conda["declared"] is True
    assert [item["file"] for item in report.conda["environments"]] == [
        "environment.yaml"
    ]


def test_name_is_recorded(make_project) -> None:
    assert environment_from(
        make_project, "name: my-env\ndependencies: [python=3.11]\n"
    ).name == ("my-env")


def test_channels_are_recorded_and_not_judged(make_project) -> None:
    """Multiple channels, or a channel most projects avoid, is a decision."""
    environment = environment_from(
        make_project, "channels:\n  - defaults\ndependencies: [python=3.11]\n"
    )

    assert environment.channels == ("defaults",)
    assert (
        ids_of(
            project(
                make_project,
                environment="channels: [defaults, conda-forge]\ndependencies: [python=3.11]\n",
            )
        )
        == set()
    )


def test_variables_are_recorded(make_project) -> None:
    environment = environment_from(
        make_project, "variables:\n  MY_VAR: value\ndependencies: [python=3.11]\n"
    )

    assert environment.variables == ("MY_VAR=value",)


# --------------------------------------------------------------------------- #
# 2. the Python pin
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("python=3.11", "==3.11.*"),
        ("python==3.11", "==3.11"),
        ("python=3.11.*", "==3.11.*"),
        ("python=3.11.4", "==3.11.4.*"),
        ("python 3.11", "==3.11.*"),
    ],
)
def test_python_equals_is_a_series_not_an_exact_pin(
    make_project, written: str, expected: str
) -> None:
    """`=3.11` is the 3.11 series. Reading it as `==3.11` invents conflicts.

    The exact pin excludes 3.11.4, so it would be provably incompatible with an
    ordinary `requires-python = ">=3.11"` and every project using both would get
    an error it does not have.
    """
    environment = environment_from(make_project, f"dependencies:\n  - {written}\n")
    declaration = environment.python_dependency

    assert declaration is not None
    assert declaration.pep440_specifier == expected


def test_python_range_is_kept_as_written(make_project) -> None:
    environment = environment_from(
        make_project, "dependencies:\n  - python>=3.11,<3.13\n"
    )
    declaration = environment.python_dependency

    assert declaration is not None
    assert declaration.pep440_specifier == ">=3.11,<3.13"


def test_python_pins_appear_in_python_requirements(make_project) -> None:
    root = project(make_project, requires=">=3.11")
    sources = {item.source: item.value for item in scan(root).python_requirements}

    assert sources["environment.yml [dependencies.python]"] == "=3.11"


def test_conda_python_is_never_reported_as_unparsable(make_project) -> None:
    """RC104 must not fire on Conda syntax.

    It is a PEP 440 rule, and `=3.11` is not PEP 440. A Conda pin is classified
    separately and translated by the rule that understands it.
    """
    root = project(make_project)

    assert "RC104" not in ids_of(root, "RC10")
    assert any(item.id == "RC100" for item in scan(root).findings)


# --------------------------------------------------------------------------- #
# 3. dependencies
# --------------------------------------------------------------------------- #


def test_simple_and_versioned_dependencies(make_project) -> None:
    environment = environment_from(
        make_project, "dependencies:\n  - scipy\n  - numpy=1.26\n"
    )
    by_name = {item.name: item for item in environment.conda_dependencies}

    assert by_name["scipy"].raw_spec == ""
    assert by_name["scipy"].pep440_specifier == ""
    assert by_name["numpy"].raw_spec == "=1.26"
    assert by_name["numpy"].pep440_specifier == "==1.26.*"


def test_channel_prefixed_dependency_records_its_channel(make_project) -> None:
    environment = environment_from(
        make_project, "dependencies:\n  - conda-forge::numpy=1.26\n"
    )
    numpy = environment.conda_dependencies[0]

    assert numpy.name == "numpy"
    assert numpy.channel == "conda-forge"
    assert numpy.raw == "conda-forge::numpy=1.26"


def test_build_string_is_separated_not_parsed(make_project) -> None:
    """`py311np123` is a build string; packaging rejects it as a version."""
    environment = environment_from(
        make_project, "dependencies:\n  - numpy=1.26=py311np123\n"
    )
    numpy = environment.conda_dependencies[0]

    assert numpy.build == "py311np123"
    assert numpy.raw_spec == "=1.26"
    assert numpy.pep440_specifier == "==1.26.*"


# --------------------------------------------------------------------------- #
# 4. the pip subsection
# --------------------------------------------------------------------------- #


def test_pip_subsection_is_parsed_as_pep508(make_project) -> None:
    environment = environment_from(make_project, BASIC)
    pip = {item.name: item for item in environment.pip_dependencies}

    assert set(pip) == {"requests", "package"}
    assert pip["requests"].raw_spec == ">=2"
    assert pip["requests"].pep440_specifier == ">=2"
    assert all(item.source == CONDA_SOURCE_PIP for item in pip.values())


def test_pip_extras_are_parsed(make_project) -> None:
    dependency = parse_pip_requirement(
        "package[extra]==1.2", file="environment.yml", line=1
    )

    assert dependency is not None
    assert dependency.name == "package"
    assert dependency.raw_spec == "==1.2"


def test_pip_markers_are_parsed(make_project) -> None:
    dependency = parse_pip_requirement(
        'tomli>=2; python_version < "3.11"', file="environment.yml", line=1
    )

    assert dependency is not None
    assert dependency.name == "tomli"
    assert dependency.raw_spec == ">=2"


def test_pip_requirements_are_compared_with_pyproject(make_project) -> None:
    """A pip entry inside a Conda file is a real requirement and is compared."""
    root = project(
        make_project,
        environment="dependencies:\n  - pip\n  - pip:\n      - requests>=2\n",
        dependencies='dependencies = ["requests<2"]',
    )
    found = {item.id for item in findings_of(root)}

    assert RC231_CONDA_DEPENDENCY_CONFLICT in found


# --------------------------------------------------------------------------- #
# 5. Python reconciliation
# --------------------------------------------------------------------------- #


def test_conda_python_compatible_with_pyproject_raises_nothing(make_project) -> None:
    root = project(
        make_project,
        environment="dependencies:\n  - python=3.11\n",
        requires=">=3.11",
    )

    assert RC230_CONDA_PYTHON_CONFLICT not in ids_of(root)


def test_conda_python_incompatible_with_pyproject_is_an_error(make_project) -> None:
    root = project(
        make_project,
        environment="dependencies:\n  - python=3.10\n",
        requires=">=3.11",
    )
    found = [
        item for item in findings_of(root) if item.id == RC230_CONDA_PYTHON_CONFLICT
    ]

    assert len(found) == 1
    assert found[0].severity is Severity.ERROR
    assert found[0].confidence is Confidence.HIGH
    assert found[0].file == "environment.yml"


def test_conda_python_reported_once_not_also_as_rc103(make_project) -> None:
    """The same disagreement must not appear under two ids."""
    root = project(
        make_project, environment="dependencies:\n  - python=3.10\n", requires=">=3.11"
    )
    ids = {item.id for item in scan(root).findings}

    assert RC230_CONDA_PYTHON_CONFLICT in ids
    assert "RC103" not in ids


def test_ci_matrix_is_not_compared_with_a_conda_pin(make_project) -> None:
    """A matrix listing several versions is not a conflict with one pin."""
    root = project(
        make_project,
        environment="dependencies:\n  - python=3.11\n",
        requires=">=3.11",
        extra_files={
            ".github/workflows/ci.yml": "name: ci\non: [push]\njobs:\n"
            "  test:\n    runs-on: ubuntu-latest\n    strategy:\n      matrix:\n"
            "        python-version: ['3.10', '3.11', '3.12']\n    steps: []\n"
        },
    )

    assert RC230_CONDA_PYTHON_CONFLICT not in ids_of(root)


# --------------------------------------------------------------------------- #
# 6. dependency reconciliation
# --------------------------------------------------------------------------- #


def test_conda_dependency_compatible_with_pyproject(make_project) -> None:
    root = project(
        make_project,
        environment="dependencies:\n  - numpy=1.26\n",
        dependencies='dependencies = ["numpy>=1.24,<2"]',
    )
    found = {item.id for item in findings_of(root)}

    assert RC231_CONDA_DEPENDENCY_CONFLICT not in found


def test_conda_dependency_incompatible_with_pyproject_is_an_error(make_project) -> None:
    root = project(
        make_project,
        environment="dependencies:\n  - numpy<2\n",
        dependencies='dependencies = ["numpy>=2"]',
    )
    found = [
        item for item in findings_of(root) if item.id == RC231_CONDA_DEPENDENCY_CONFLICT
    ]

    assert len(found) == 1
    assert found[0].severity is Severity.ERROR


def test_conda_dependency_incompatible_with_requirements_txt(make_project) -> None:
    root = project(
        make_project,
        environment="dependencies:\n  - numpy=1.26\n",
        extra_files={"requirements.txt": "numpy==2.0\n"},
    )

    assert RC231_CONDA_DEPENDENCY_CONFLICT in ids_of(root)


def test_conda_dependency_merely_different_is_info(make_project) -> None:
    root = project(
        make_project,
        environment="dependencies:\n  - requests\n",
        dependencies='dependencies = ["requests>=2.31"]',
    )
    found = [
        item for item in findings_of(root) if item.id == RC232_CONDA_DEPENDENCY_DIFFERS
    ]

    assert len(found) == 1
    assert found[0].severity is Severity.INFO


def test_duplicate_conda_and_pip_declaration_is_info(make_project) -> None:
    root = project(
        make_project,
        environment="dependencies:\n  - requests\n  - pip\n  - pip:\n      - requests\n",
    )
    found = [item for item in findings_of(root) if item.id == RC233_CONDA_PIP_DUPLICATE]

    assert len(found) == 1
    assert found[0].severity is Severity.INFO
    assert "requests" in found[0].message


def test_pip_subsection_without_pip_is_info(make_project) -> None:
    root = project(
        make_project, environment="dependencies:\n  - pip:\n      - requests\n"
    )
    found = [
        item for item in findings_of(root) if item.id == RC235_CONDA_PIP_NOT_DECLARED
    ]

    assert len(found) == 1
    assert found[0].severity is Severity.INFO


def test_pip_declared_raises_nothing(make_project) -> None:
    root = project(
        make_project, environment="dependencies:\n  - pip\n  - pip:\n      - requests\n"
    )

    assert RC235_CONDA_PIP_NOT_DECLARED not in ids_of(root)


def test_build_system_requirements_are_never_compared(make_project) -> None:
    """A build requirement and a development environment are different things.

    Found by the external validation: tqdm lists `setuptools` in its
    `environment.yml` and `setuptools>=42` in `[build-system] requires`. The
    build system is installed in an isolated build environment to assemble the
    wheel, so an environment that says nothing about it is not disagreeing with
    anything, and reporting it was a false positive.
    """
    root = project(
        make_project,
        environment="dependencies:\n  - setuptools\n",
    )
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    pyproject = pyproject.replace(
        'requires = ["setuptools"]', 'requires = ["setuptools>=42"]'
    )
    (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")

    found = {item.id for item in findings_of(root)}
    assert RC232_CONDA_DEPENDENCY_DIFFERS not in found
    assert RC231_CONDA_DEPENDENCY_CONFLICT not in found


def test_the_compared_section_is_named(make_project) -> None:
    """A reader cannot judge a divergence without knowing which section it is."""
    root = project(
        make_project,
        environment="dependencies:\n  - ipywidgets\n",
        dependencies='[project.optional-dependencies]\nnotebook = ["ipywidgets>=6"]',
    )
    found = [
        item for item in findings_of(root) if item.id == RC232_CONDA_DEPENDENCY_DIFFERS
    ]

    assert len(found) == 1
    assert "notebook" in found[0].message


# --------------------------------------------------------------------------- #
# 7. no opinionated checks
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "environment",
    [
        "channels: [defaults]\ndependencies: [python=3.11]\n",
        "channels: [conda-forge]\ndependencies: [python=3.11]\n",
        "dependencies: [python=3.11, numpy, scipy, requests]\n",
        "dependencies: [python=3.11]\n",
        # A pip subsection is a normal thing to have. Listing pip as well is the
        # ordinary way to write it, and nothing about it is a finding.
        "dependencies:\n  - python=3.11\n  - pip\n  - pip:\n      - anything\n",
    ],
    ids=["defaults", "conda-forge", "unpinned", "no-channels", "pip-subsection"],
)
def test_valid_conda_choices_are_never_findings(make_project, environment: str) -> None:
    """None of these is a defect, so none of them may produce a finding.

    Channel choice, pinning and the presence of a pip subsection are project
    decisions. A tool that reported them would be unreadable within a week.
    """
    root = project(make_project, environment=environment, requires=">=3.11")

    assert ids_of(root) == set(), sorted(ids_of(root))


def test_missing_lockfile_is_not_a_finding(make_project) -> None:
    root = project(make_project)

    assert "RC116" not in ids_of(root, "RC116")


# --------------------------------------------------------------------------- #
# 8. selectors
# --------------------------------------------------------------------------- #


def test_win_selector_is_recorded(make_project) -> None:
    environment = environment_from(make_project, "dependencies:\n  - numpy  # [win]\n")
    numpy = environment.conda_dependencies[0]

    assert numpy.selector == ("win",)
    assert numpy.is_conditional is True


def test_osx_selector_is_recorded(make_project) -> None:
    environment = environment_from(make_project, "dependencies:\n  - pyobjc # [osx]\n")

    assert environment.conda_dependencies[0].selector == ("osx",)


def test_different_selectors_are_never_compared_as_universal(make_project) -> None:
    """A macOS-only pin must not conflict with a cross-platform requirement.

    The two do not apply to the same platform, so any conflict between them
    would be a conflict on no platform at all.
    """
    root = project(
        make_project,
        environment="dependencies:\n  - pyobjc>=11  # [osx]\n",
        dependencies='dependencies = ["pyobjc<10"]',
    )
    found = {item.id for item in findings_of(root)}

    assert RC231_CONDA_DEPENDENCY_CONFLICT not in found


def test_universal_conda_dependency_still_conflicts(make_project) -> None:
    """The selector rule must not become a blanket exemption."""
    root = project(
        make_project,
        environment="dependencies:\n  - numpy<2\n",
        dependencies='dependencies = ["numpy>=2"]',
    )

    assert RC231_CONDA_DEPENDENCY_CONFLICT in ids_of(root)


def test_selector_synonyms_are_normalised(make_project) -> None:
    assert selectors_by_line("- numpy  # [win32]\n") == {1: ("win",)}
    assert selectors_by_line("- pyobjc # [unix]\n") == {1: ("osx",)}


def test_no_selector_on_another_line_is_not_attached(make_project) -> None:
    """A selector belongs to its own line, not to the whole file."""
    environment = environment_from(
        make_project, "dependencies:\n  - numpy\n  - pyobjc # [osx]\n"
    )
    by_name = {item.name: item for item in environment.conda_dependencies}

    assert by_name["numpy"].selector == ()
    assert by_name["pyobjc"].selector == ("osx",)


# --------------------------------------------------------------------------- #
# 9. YAML behaviour
# --------------------------------------------------------------------------- #


def test_malformed_yaml_is_reported_not_raised(make_project) -> None:
    root = project(make_project, environment="name: demo\ndependencies: [unclosed\n")
    found = [item for item in findings_of(root) if item.id == RC234_CONDA_UNREADABLE]

    assert len(found) == 1
    assert found[0].severity is Severity.ERROR
    assert "could not be parsed" in found[0].message


def test_unsupported_tag_is_reported_not_raised(make_project) -> None:
    root = project(make_project, environment="dependencies:\n  - !Evil python=3.11\n")
    found = [item for item in findings_of(root) if item.id == RC234_CONDA_UNREADABLE]

    assert len(found) == 1
    assert found[0].severity is Severity.ERROR


def test_custom_tags_cannot_construct_objects(make_project) -> None:
    """safe_load only: a tag must never reach a Python constructor."""
    payload = "dependencies:\n  - !!python/object/apply:os.system ['echo pwned']\n"
    root = project(make_project, environment=payload)
    environment = environment_of(root)

    assert environment.parse_error is not None
    assert environment.dependencies == ()


def test_anchors_and_aliases_are_resolved(make_project) -> None:
    environment = environment_from(
        make_project,
        "base: &base\n  - conda-forge\nchannels: *base\ndependencies: [python=3.11]\n",
    )

    assert environment.channels == ("conda-forge",)
    assert environment.parse_error is None


def _environment_with_bytes(make_project, name: str, payload: bytes) -> Path:
    """A project whose environment file has exact bytes on disk.

    The BOM and the CRLF endings have to be written as bytes: a text write would
    normalise them away and the test would prove nothing.
    """
    root = make_project({}, name=name)
    (root / "environment.yml").write_bytes(payload)
    return root


def test_utf8_bom_is_tolerated(make_project) -> None:
    root = _environment_with_bytes(
        make_project,
        "bom",
        b"\xef\xbb\xbfname: bom\ndependencies:\n  - python=3.11\n",
    )

    environment = parse_environment(root / "environment.yml", root)
    assert environment.name == "bom"
    assert environment.parse_error is None
    assert environment.python_dependency is not None


def test_crlf_line_endings_are_tolerated(make_project) -> None:
    root = _environment_with_bytes(
        make_project, "crlf", b"name: crlf\r\ndependencies:\r\n  - python=3.11\r\n"
    )

    environment = parse_environment(root / "environment.yml", root)
    assert environment.name == "crlf"
    assert environment.parse_error is None
    assert environment.python_dependency is not None


def test_undecodable_bytes_are_reported_not_raised(make_project) -> None:
    root = _environment_with_bytes(
        make_project,
        "binary",
        b"name: \xff\xfe not utf-8\ndependencies: [python=3.11]\n",
    )

    environment = parse_environment(root / "environment.yml", root)
    assert environment.parse_error is not None
    assert "UTF-8" in environment.parse_error


def test_unicode_is_preserved(make_project) -> None:
    environment = environment_from(
        make_project, "name: ambiente-\u00e7\u00e3o\ndependencies:\n  - m\u00f3dulo\n"
    )

    assert environment.name == "ambiente-\u00e7\u00e3o"
    assert environment.conda_dependencies[0].name == "m\u00f3dulo"


def test_unknown_top_level_key_is_reported_as_unsupported(make_project) -> None:
    root = project(
        make_project,
        environment="name: demo\nbuild:\n  number: 1\ndependencies: [python=3.11]\n",
    )
    found = [item for item in findings_of(root) if item.id == RC234_CONDA_UNREADABLE]

    assert len(found) == 1
    assert found[0].severity is Severity.WARNING
    assert "build" in found[0].message


def test_empty_file_is_reported(make_project) -> None:
    root = project(make_project, environment="")
    found = [item for item in findings_of(root) if item.id == RC234_CONDA_UNREADABLE]

    assert len(found) == 1
    assert "empty" in found[0].message


def test_top_level_list_is_reported(make_project) -> None:
    root = project(make_project, environment="- python=3.11\n")
    found = [item for item in findings_of(root) if item.id == RC234_CONDA_UNREADABLE]

    assert len(found) == 1
    assert "mapping" in found[0].message


def test_line_numbers_are_real(make_project) -> None:
    environment = environment_from(
        make_project, "name: demo\ndependencies:\n  - numpy\n  - scipy\n"
    )
    by_name = {item.name: item for item in environment.conda_dependencies}

    assert by_name["numpy"].line == 3
    assert by_name["scipy"].line == 4


# --------------------------------------------------------------------------- #
# 10. which files count
# --------------------------------------------------------------------------- #


def test_extra_environment_read_only_when_the_project_names_it(make_project) -> None:
    files = {
        "environment.yml": "name: main\ndependencies: [python=3.11]\n",
        "environment-dev.yml": "name: dev\ndependencies: [python=3.12]\n",
    }
    root = make_project(files)
    report = scan(root)

    assert [item["file"] for item in report.conda["environments"]] == [
        "environment.yml"
    ]
    assert explicitly_named_environment_files(root) == frozenset()


def test_extra_environment_read_when_the_readme_names_it(make_project) -> None:
    files = {
        "environment.yml": "name: main\ndependencies: [python=3.11]\n",
        "environment-dev.yml": "name: dev\ndependencies: [python=3.12]\n",
        "README.md": "# demo\n\nCreate the dev environment:\n\n"
        "```bash\nconda env create -f environment-dev.yml\n```\n",
    }
    root = make_project(files)
    report = scan(root)

    assert "environment-dev.yml" in explicitly_named_environment_files(root)
    assert sorted(item["file"] for item in report.conda["environments"]) == [
        "environment-dev.yml",
        "environment.yml",
    ]


def test_extra_environment_read_when_a_workflow_names_it(make_project) -> None:
    files = {
        "environment.yml": "name: main\ndependencies: [python=3.11]\n",
        "environment-test.yml": "name: test\ndependencies: [python=3.12]\n",
        ".github/workflows/ci.yml": "name: ci\non: [push]\njobs:\n  test:\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: conda env create -f environment-test.yml\n",
    }
    root = make_project(files)

    assert "environment-test.yml" in explicitly_named_environment_files(root)


def test_arbitrary_yaml_is_never_guessed_to_be_an_environment(make_project) -> None:
    root = make_project(
        {
            "environment.yml": "name: main\ndependencies: [python=3.11]\n",
            "random.yml": "name: not-an-environment\ndependencies: [python=3.9]\n",
            "ci/build.yml": "name: also-not\n",
        }
    )
    report = scan(root)

    assert [item["file"] for item in report.conda["environments"]] == [
        "environment.yml"
    ]


def test_project_without_conda_has_an_empty_section(make_project) -> None:
    root = make_project(
        {"pyproject.toml": PYPROJECT.format(requires=">=3.11", dependencies="")}
    )
    report = scan(root)

    assert report.conda["declared"] is False
    assert report.conda["environments"] == []
    assert report.conda["summary"]["environments"] == 0


# --------------------------------------------------------------------------- #
# 11. the version translation, tested directly
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("conda", "expected"),
    [
        ("=3.11", "==3.11.*"),
        ("=1.26", "==1.26.*"),
        ("=1.26.4", "==1.26.4.*"),
        ("=3.11.*", "==3.11.*"),
        ("==3.11", "==3.11"),
        (">=1.26", ">=1.26"),
        (">=3.11,<3.13", ">=3.11,<3.13"),
        ("!=1.26", "!=1.26"),
        ("~=1.26", "~=1.26"),
        ("1.26", "==1.26"),
        ("", ""),
    ],
)
def test_translation_keeps_the_meaning(conda: str, expected: str) -> None:
    assert conda_to_pep440(conda) == expected


@pytest.mark.parametrize(
    "conda",
    ["=1.26=py311np123", "abc", "=3.11.*.*", ">=1.0.*", ">=", "=="],
)
def test_translation_refuses_what_it_cannot_express(conda: str) -> None:
    """None means unknown, and unknown is never treated as agreement."""
    assert conda_to_pep440(conda) is None


def test_untranslatable_dependency_is_never_compared(make_project) -> None:
    root = project(
        make_project,
        environment="dependencies:\n  - numpy>=1.26,<2.0rc1\n",
        dependencies='dependencies = ["numpy>=2"]',
    )
    found = {item.id for item in findings_of(root)}

    assert RC231_CONDA_DEPENDENCY_CONFLICT not in found
    assert RC232_CONDA_DEPENDENCY_DIFFERS not in found


def test_widening_is_an_identity_not_an_approximation() -> None:
    """`==3.10.*` and `>=3.10,<3.11` must accept exactly the same versions.

    If they ever diverge, the widening would start inventing conflicts.
    """
    candidates = [
        "3.9.9",
        "3.10",
        "3.10.0",
        "3.10.4",
        "3.10.99",
        "3.11",
        "3.11.0",
        "4.0",
    ]
    for wildcard, interval in (
        ("==3.10.*", ">=3.10,<3.11"),
        ("==3.11.*", ">=3.11,<3.12"),
        ("==1.26.4.*", ">=1.26.4,<1.26.5"),
    ):
        left, right = SpecifierSet(wildcard), SpecifierSet(interval)
        for candidate in candidates:
            version = Version(candidate)
            assert (version in left) == (version in right), (wildcard, candidate)


def test_widening_leaves_everything_else_alone() -> None:
    for spec in ("==3.*", ">=1.26,<2", "", "!=3.10"):
        assert widen_wildcard_pin(spec) == spec


# --------------------------------------------------------------------------- #
# 12. reports
# --------------------------------------------------------------------------- #


def test_json_report_carries_the_conda_section(make_project) -> None:
    from reprocheck.reporters import write_report

    root = project(make_project)
    destination = root.parent / "out" / "report.json"
    written = write_report(scan(root), destination)
    data = json.loads(written.read_text(encoding="utf-8"))

    assert data["conda"]["declared"] is True
    assert data["conda"]["summary"]["dependencies"] == 4
    environment = data["conda"]["environments"][0]
    assert environment["name"] == "demo"
    assert environment["channels"] == ["conda-forge", "defaults"]
    assert environment["dependencies"][0]["name"] == "python"
    # The facts block carries the same information for consumers that read it.
    assert data["facts"]["conda"]["environments"] == data["conda"]["environments"]


def test_markdown_has_a_conda_section(make_project) -> None:
    from reprocheck.reporters import render_markdown

    text = render_markdown(scan(project(make_project)))

    assert "## Conda" in text
    assert "environment.yml" in text
    assert "conda-forge" in text
    assert "Static facts from the environment declarations" in text


def test_markdown_omits_the_section_without_conda(make_project) -> None:
    from reprocheck.reporters import render_markdown

    root = make_project(
        {"pyproject.toml": PYPROJECT.format(requires=">=3.11", dependencies="")}
    )

    assert "## Conda" not in render_markdown(scan(root))


def test_markdown_lists_findings_not_every_package(make_project) -> None:
    from reprocheck.reporters import render_markdown

    many = "dependencies:\n" + "".join(f"  - pkg{i}\n" for i in range(60))
    text = render_markdown(
        scan(project(make_project, environment=many, requires=">=3.11"))
    )

    assert "## Conda" in text
    assert "pkg17" not in text, "a 60-package dump would bury the useful part"
    assert "Conda dependencies: 60" in text


def test_terminal_has_a_conda_block(make_project) -> None:
    from reprocheck.reporters import format_report

    text = format_report(scan(project(make_project)), "report.json")

    assert "Conda" in text
    assert "environment files: 1" in text
    assert "pip dependencies: 2" in text
    assert "channels: 2" in text
    assert "python: python=3.11" in text


def test_terminal_omits_the_block_without_conda(make_project) -> None:
    from reprocheck.reporters import format_report

    root = make_project(
        {"pyproject.toml": PYPROJECT.format(requires=">=3.11", dependencies="")}
    )

    assert "environment files:" not in format_report(scan(root), "report.json")


def test_terminal_separates_conda_findings(make_project) -> None:
    from reprocheck.reporters import format_report

    root = project(
        make_project, environment="dependencies:\n  - python=3.10\n", requires=">=3.11"
    )
    text = format_report(scan(root), "report.json")

    assert "Conda findings" in text


# --------------------------------------------------------------------------- #
# 13. the diff
# --------------------------------------------------------------------------- #


def _report_dict(root: Path) -> dict:
    return scan(root).to_dict()


def test_diff_sees_a_changed_dependency_spec(make_project) -> None:
    from reprocheck.diff import compare_reports

    before = _report_dict(project(make_project, name="before"))
    after = _report_dict(
        project(
            make_project, environment="dependencies:\n  - numpy=1.26\n", name="after"
        )
    )
    changes = compare_reports(before, after).conda

    assert any(item.kind == "changed" for item in changes)


def test_diff_sees_a_changed_channel_set(make_project) -> None:
    from reprocheck.diff import compare_reports

    before = _report_dict(
        project(
            make_project,
            environment="channels: [conda-forge]\ndependencies: [python=3.11]\n",
            name="before",
        )
    )
    after = _report_dict(
        project(
            make_project,
            environment="channels: [defaults]\ndependencies: [python=3.11]\n",
            name="after",
        )
    )
    changes = compare_reports(before, after).conda

    assert any("channels" in (item.changed_fields or ()) for item in changes)


def test_diff_sees_a_changed_python_pin(make_project) -> None:
    from reprocheck.diff import compare_reports

    before = _report_dict(
        project(
            make_project, environment="dependencies: [python=3.11]\n", name="before"
        )
    )
    after = _report_dict(
        project(make_project, environment="dependencies: [python=3.12]\n", name="after")
    )
    changes = compare_reports(before, after).conda

    assert any("python" in (item.changed_fields or ()) for item in changes)


def test_diff_sees_an_added_dependency(make_project) -> None:
    from reprocheck.diff import compare_reports

    before = _report_dict(
        project(
            make_project, environment="dependencies: [python=3.11]\n", name="before"
        )
    )
    after = _report_dict(
        project(
            make_project,
            environment="dependencies: [python=3.11, numpy]\n",
            name="after",
        )
    )
    changes = compare_reports(before, after).conda

    assert any(item.kind == "added" and "numpy" in item.key for item in changes)


def test_diff_sees_a_changed_pip_requirement(make_project) -> None:
    from reprocheck.diff import compare_reports

    before = _report_dict(
        project(
            make_project,
            environment="dependencies: [pip, {pip: [requests>=2]}]\n",
            name="before",
        )
    )
    after = _report_dict(
        project(
            make_project,
            environment="dependencies: [pip, {pip: [requests>=3]}]\n",
            name="after",
        )
    )
    changes = compare_reports(before, after).conda

    assert any(item.kind == "changed" for item in changes)


def test_diff_sees_an_added_environment(make_project) -> None:
    from reprocheck.diff import compare_reports

    before = _report_dict(
        make_project(
            {"pyproject.toml": PYPROJECT.format(requires=">=3.11", dependencies="")},
            name="before",
        )
    )
    after = _report_dict(project(make_project, name="after"))
    changes = compare_reports(before, after).conda

    assert any(item.kind == "added" and item.key.startswith("env:") for item in changes)


def test_diff_ignores_channel_order_and_formatting(make_project) -> None:
    """Order and formatting are not facts about the declaration."""
    from reprocheck.diff import compare_reports

    before = _report_dict(
        project(
            make_project,
            environment="channels: [conda-forge, defaults]\ndependencies: [python=3.11]\n",
            name="before",
        )
    )
    after = _report_dict(
        project(
            make_project,
            environment="# a new comment\nchannels:\n  - defaults\n  - conda-forge\n"
            "dependencies:\n\n  - python=3.11   \n",
            name="after",
        )
    )
    diff = compare_reports(before, after)

    assert diff.conda == ()
    assert not diff.has_changes


# --------------------------------------------------------------------------- #
# 14. no fix, no write, no network
# --------------------------------------------------------------------------- #


def test_suggest_creates_no_fix_for_any_conda_finding(make_project) -> None:
    from reprocheck.scanner import collect_facts
    from reprocheck.suggest.engine import suggest
    from reprocheck.suggest.models import Safety

    root = project(
        make_project,
        environment="dependencies:\n  - python=3.10\n  - numpy<2\n  - requests\n"
        "  - pip:\n      - requests\n",
        requires=">=3.11",
        dependencies='dependencies = ["numpy>=2"]',
    )
    report = scan(root)
    suggestions = suggest(collect_facts(root), report)

    conda_items = [
        item for item in suggestions.suggestions if item.finding_id.startswith("RC23")
    ]
    assert conda_items, "the Conda findings should be answerable"
    assert all(item.safety is Safety.MANUAL_ONLY for item in conda_items)
    assert not any(item.has_patch for item in conda_items)


def test_conda_files_are_never_written(make_project) -> None:
    root = project(
        make_project, environment="dependencies:\n  - python=3.10\n", requires=">=3.11"
    )
    before = snapshot(root)

    scan(root)

    assert snapshot(root) == before


def test_cli_scan_leaves_the_environment_untouched(make_project, tmp_path) -> None:

    root = project(make_project, environment=BASIC)
    before = snapshot(root)
    destination = tmp_path / "report.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "reprocheck",
            "scan",
            str(root),
            "--json",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert snapshot(root) == before
    assert (
        json.loads(destination.read_text(encoding="utf-8"))["conda"]["declared"] is True
    )


def test_no_conda_subprocess_is_ever_started(make_project) -> None:
    """Conda is never installed, run, or resolved."""
    root = project(make_project, environment=BASIC)
    seen: list[list[str]] = []
    original = subprocess.run

    def record(command, *args, **kwargs):
        seen.append([str(part) for part in command])
        return original(command, *args, **kwargs)

    subprocess.run = record  # type: ignore[assignment]
    try:
        scan(root)
    finally:
        subprocess.run = original  # type: ignore[assignment]

    executed = " ".join(
        part
        for call in seen
        for part in call
        if os.path.basename(part).lower().startswith("conda")
    )
    assert not executed, executed


def test_no_network_module_is_imported_by_the_conda_path(make_project) -> None:
    """The static path must not reach for a socket."""
    root = project(make_project, environment=BASIC)
    code = (
        "import socket, sys\n"
        "def _forbidden(*a, **k):\n"
        "    raise AssertionError('the static Conda path opened a socket')\n"
        "socket.create_connection = _forbidden\n"
        "socket.socket.connect = _forbidden\n"
        f"sys.path.insert(0, {str(Path(__file__).parent.parent / 'src')!r})\n"
        "from reprocheck.scanner import scan\n"
        f"report = scan({str(root)!r})\n"
        "assert report.conda['declared'] is True\n"
        "print('no socket')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )

    assert completed.returncode == 0, completed.stderr[-1500:]
    assert "no socket" in completed.stdout


# --------------------------------------------------------------------------- #
# 15. reproduction says what it did not do
# --------------------------------------------------------------------------- #


def test_verdict_lists_conda_as_not_verified(make_project) -> None:
    root = project(make_project)
    verdict = scan(root).verdict

    assert any("Conda" in item.item for item in verdict.not_verified), [
        item.item for item in verdict.not_verified
    ]
    assert any(
        "not reproduced" in item.reason or "no conda process" in item.reason
        for item in verdict.not_verified
    )


def test_verdict_omits_conda_without_an_environment(make_project) -> None:
    root = make_project(
        {"pyproject.toml": PYPROJECT.format(requires=">=3.11", dependencies="")}
    )

    assert not any("Conda" in item.item for item in scan(root).verdict.not_verified)


# --------------------------------------------------------------------------- #
# 16. the check in isolation
# --------------------------------------------------------------------------- #


def test_check_returns_nothing_for_a_facts_without_conda() -> None:
    facts = Facts(project=ProjectScan(name="x", path=".", python_file_count=1))

    assert check_conda(facts) == []


def test_check_uses_only_facts() -> None:
    """The check is a pure function of the facts: no filesystem access."""
    environment = CondaEnvironment(
        file="environment.yml",
        name="demo",
        channels=("conda-forge",),
        dependencies=(
            CondaDependency(
                name="python",
                raw="python=3.10",
                raw_spec="=3.10",
                pep440_specifier="==3.10.*",
                is_python=True,
            ),
        ),
    )
    facts = Facts(
        project=ProjectScan(name="x", path=".", python_file_count=1),
        conda_environments=[environment],
        python_requirements=[
            PythonRequirement(
                source="pyproject.toml [project.requires-python]", value=">=3.11"
            )
        ],
    )
    found = {item.id for item in check_conda(facts)}

    assert RC230_CONDA_PYTHON_CONFLICT in found


def test_facts_serialise_to_json_primitives(make_project) -> None:
    environment = environment_from(
        make_project, "channels: [conda-forge]\ndependencies:\n  - numpy=1.26\n"
    )
    data = environment.to_dict()
    encoded = json.dumps(data)

    assert isinstance(data["channels"], list)
    assert isinstance(data["dependencies"], list)
    assert "conda-forge" in encoded


def test_scan_conda_summary_counts_separate_pip(make_project) -> None:
    root = project(make_project)
    summary = scan_conda(
        root, explicitly_named=explicitly_named_environment_files(root)
    ).summary

    assert summary == {
        "environments": 1,
        "dependencies": 4,
        "pip_dependencies": 2,
        "channels": 2,
    }


def test_conda_source_constants_are_distinct() -> None:
    """The report must never present a Conda file as a requirements file."""
    assert CONDA_SOURCE_DEPENDENCY == "conda"
    assert CONDA_SOURCE_PIP == "conda-pip"
    assert CONDA_SOURCE_DEPENDENCY != CONDA_SOURCE_PIP
