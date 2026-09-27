"""Tests for the dependency consistency checks (RC200-RC210)."""

from __future__ import annotations

from reprocheck.scanner import scan


def _ids(root) -> set[str]:
    return {finding.id for finding in scan(root).findings}


def _findings(root, identifier: str) -> list:
    return [item for item in scan(root).findings if item.id == identifier]


def _finding(root, identifier: str):
    found = _findings(root, identifier)
    assert found, f"{identifier} not reported"
    return found[0]


def _pyproject(dependencies: str, groups: str = "", optional: str = "") -> str:
    return f"[project]\nname = 'x'\ndependencies = [{dependencies}]\n{optional}{groups}"


# --------------------------------------------------------------------------- #
# 1. conflicting exact pins
# --------------------------------------------------------------------------- #


def test_conflicting_exact_pins_between_runtime_and_dev(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": _pyproject(
                "'numpy==1.23.5'",
                groups="[dependency-groups]\ndev = ['numpy==2.0.0']\n",
            )
        }
    )
    finding = _finding(root, "RC200")

    assert finding.severity.name == "WARNING"
    assert finding.confidence.name == "HIGH"
    assert "numpy" in finding.message
    assert "==1.23.5" in finding.message
    assert "==2.0.0" in finding.message
    assert "RC201" not in _ids(root)


def test_conflicting_exact_pins_in_the_same_file(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": "numpy==1.23.5\n",
            "requirements-dev.txt": "numpy==2.0.0\n",
        }
    )
    assert "RC200" in _ids(root)


# --------------------------------------------------------------------------- #
# 2. compatible but different
# --------------------------------------------------------------------------- #


def test_overlapping_ranges_are_compatible(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": _pyproject(
                "'numpy>=1.23,<2'",
                groups="[dependency-groups]\ndev = ['numpy>=1.24']\n",
            )
        }
    )
    ids = _ids(root)
    assert "RC200" not in ids
    assert "RC201" not in ids
    assert "RC205" in ids


def test_wider_range_is_not_a_conflict(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": _pyproject(
                "'numpy>=1'",
                groups="[dependency-groups]\ndev = ['numpy>=1.2']\n",
            )
        }
    )
    assert not {"RC200", "RC201"} & _ids(root)


# --------------------------------------------------------------------------- #
# 3. disjoint constraints
# --------------------------------------------------------------------------- #


def test_disjoint_ranges(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": _pyproject(
                "'numpy<2'",
                groups="[dependency-groups]\ndev = ['numpy>=2']\n",
            )
        }
    )
    finding = _finding(root, "RC201")

    assert finding.severity.name == "ERROR"
    assert finding.confidence.name == "HIGH"
    assert "candidates" in str(finding.evidence)


def test_disjoint_in_a_single_requirements_file(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": "numpy<2\n",
            "requirements-dev.txt": "numpy>=2.1\n",
        }
    )
    assert "RC201" in _ids(root)


# --------------------------------------------------------------------------- #
# 4. identical declaration in two places
# --------------------------------------------------------------------------- #


def test_identical_declaration_is_redundant_not_a_conflict(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": _pyproject("'requests>=2.31'"),
            "requirements.txt": "requests>=2.31\n",
        }
    )
    finding = _finding(root, "RC202")

    assert finding.severity.name == "INFO"
    assert finding.confidence.name == "HIGH"
    assert "declared twice" in finding.message
    assert "RC200" not in _ids(root)
    assert "RC201" not in _ids(root)


# --------------------------------------------------------------------------- #
# 5. compatible but divergent
# --------------------------------------------------------------------------- #


def test_divergent_declarations(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": "requests>=2.28\n",
            "pyproject.toml": _pyproject("'requests<3'"),
        }
    )
    ids = _ids(root)
    assert "RC202" in ids
    assert "RC200" not in ids
    assert "RC201" not in ids


def test_incompatible_declarations_are_not_double_reported(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": "requests==2.28.0\n",
            "pyproject.toml": _pyproject("'requests==2.31.0'"),
        }
    )
    ids = _ids(root)
    assert "RC200" in ids
    assert "RC202" not in ids


# --------------------------------------------------------------------------- #
# 6./7. requirements include
# --------------------------------------------------------------------------- #


def test_existing_include_produces_no_finding(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": "-r requirements/base.txt\n",
            "requirements/base.txt": "numpy==1.23.5\n",
        }
    )
    assert not {"RC206", "RC207"} & _ids(root)


def test_missing_include(make_project) -> None:
    root = make_project({"requirements.txt": "-r requirements/base.txt\n"})
    finding = _finding(root, "RC206")

    assert finding.severity.name == "WARNING"
    assert finding.confidence.name == "HIGH"
    assert "requirements/base.txt" in finding.message
    assert finding.line == 1


# --------------------------------------------------------------------------- #
# 8. constraints
# --------------------------------------------------------------------------- #


def test_existing_constraint_file(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": "-c constraints.txt\nrequests\n",
            "constraints.txt": "requests==2.31.0\n",
        }
    )
    assert "RC207" not in _ids(root)


def test_missing_constraint_file(make_project) -> None:
    root = make_project({"requirements.txt": "-c constraints.txt\n"})
    finding = _finding(root, "RC207")

    assert finding.severity.name == "WARNING"
    assert finding.confidence.name == "HIGH"
    assert "constraints.txt" in finding.message


def test_constraint_contradicting_a_pin(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": "-c constraints.txt\nrequests==2.28.0\n",
            "constraints.txt": "requests>=2.31\n",
        }
    )
    assert "RC201" in _ids(root)


# --------------------------------------------------------------------------- #
# 9./10./11. VCS references
# --------------------------------------------------------------------------- #


def test_vcs_pinned_to_a_commit(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": (
                "mypkg @ git+https://github.com/org/mypkg"
                "@0123456789abcdef0123456789abcdef01234567\n"
            )
        }
    )
    finding = _finding(root, "RC208")

    assert finding.severity.name == "INFO"
    assert "0123456789abcdef0123456789abcdef01234567" in finding.message
    assert "RC209" not in _ids(root)


def test_vcs_pinned_to_a_branch(make_project) -> None:
    root = make_project(
        {"requirements.txt": "mypkg @ git+https://github.com/org/mypkg@main\n"}
    )
    finding = _finding(root, "RC209")

    assert finding.severity.name == "WARNING"
    assert finding.confidence.name == "HIGH"
    assert "main" in finding.message
    assert "RC208" not in _ids(root)


def test_vcs_without_ref(make_project) -> None:
    root = make_project(
        {"requirements.txt": "mypkg @ git+https://github.com/org/mypkg\n"}
    )
    finding = _finding(root, "RC209")

    assert "no ref" in finding.message


# --------------------------------------------------------------------------- #
# 12. direct URL
# --------------------------------------------------------------------------- #


def test_direct_url_dependency(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": (
                "https://example.com/packages/numpy-1.23.5-py3-none-any.whl\n"
            )
        }
    )
    finding = _finding(root, "RC208")

    assert finding.severity.name == "INFO"
    assert "external URL" in finding.message


# --------------------------------------------------------------------------- #
# 13./14. local path dependencies
# --------------------------------------------------------------------------- #


def test_local_path_inside_the_project(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": "-e ./vendor/mypkg\n",
            "vendor/mypkg/pyproject.toml": "[project]\nname = 'mypkg'\n",
        }
    )
    assert "RC210" not in _ids(root)


def test_missing_local_path(make_project) -> None:
    root = make_project({"requirements.txt": "-e ../outside/mypkg\n"})
    finding = _finding(root, "RC210")

    assert finding.severity.name == "WARNING"
    assert finding.confidence.name == "HIGH"
    assert "../outside/mypkg" in finding.message


def test_absolute_local_path_mentions_rc120(make_project) -> None:
    root = make_project(
        {"requirements.txt": "mypkg @ file:///C:/Users/someone/mypkg\n"}
    )
    finding = _finding(root, "RC210")

    assert "absolute" in finding.message
    assert "RC120" in finding.message


# --------------------------------------------------------------------------- #
# 16. extras
# --------------------------------------------------------------------------- #


def test_different_extras_are_not_a_conflict(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": _pyproject(
                "'requests[socks]>=2.31'",
                groups="[dependency-groups]\ndev = ['requests[security]>=2.31']\n",
            )
        }
    )
    ids = _ids(root)
    assert not {"RC200", "RC201"} & ids
    assert "RC205" in ids


# --------------------------------------------------------------------------- #
# 17. environment markers
# --------------------------------------------------------------------------- #


def test_different_markers_are_not_compared(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": _pyproject(
                "'numpy==1.23.5; python_version < \"3.12\"', "
                "'numpy==2.0.0; python_version >= \"3.12\"'"
            )
        }
    )
    assert not {"RC200", "RC201", "RC202"} & _ids(root)


# --------------------------------------------------------------------------- #
# 18. optional dependencies
# --------------------------------------------------------------------------- #


def test_optional_extra_pin_difference_is_downgraded(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": _pyproject(
                "'numpy==1.23.5'",
                optional="[project.optional-dependencies]\nnew = ['numpy==2.0.0']\n",
            )
        }
    )
    ids = _ids(root)
    # An opt-in extra may pin a different version on purpose, so the exact-pin
    # rule (RC200) does not apply; the conflict is only reported, downgraded.
    assert "RC200" not in ids
    finding = _finding(root, "RC201")
    assert finding.severity.name == "WARNING"
    assert finding.confidence.name == "MEDIUM"
    assert "opt-in" in finding.message


def test_optional_extra_reach_is_visible(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": _pyproject(
                "'numpy<2'",
                optional="[project.optional-dependencies]\nnew = ['numpy>=2']\n",
            )
        }
    )
    finding = _finding(root, "RC201")

    assert finding.severity.name == "WARNING"
    assert finding.confidence.name == "MEDIUM"


# --------------------------------------------------------------------------- #
# 19. dependency groups
# --------------------------------------------------------------------------- #


def test_dependency_groups_are_classified(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": _pyproject(
                "'requests'",
                groups="[dependency-groups]\ndev = ['ruff']\ntests = ['pytest']\n",
            )
        }
    )
    summary = scan(root).dependencies["summary"]

    assert summary["dev"] == 1  # type: ignore[index]
    assert summary["test"] == 1  # type: ignore[index]
    assert summary["runtime"] == 1  # type: ignore[index]


# --------------------------------------------------------------------------- #
# 20. include cycles
# --------------------------------------------------------------------------- #


def test_include_cycle_is_survived(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": "-r requirements/extra.txt\nrequests\n",
            "requirements/extra.txt": "-r requirements.txt\nnumpy==1.23.5\n",
        }
    )
    names = {
        item["name"]
        for item in scan(root).dependencies["declarations"]  # type: ignore[index]
    }

    assert names == {"requests", "numpy"}


# --------------------------------------------------------------------------- #
# 21. name normalisation
# --------------------------------------------------------------------------- #


def test_name_normalisation_prevents_false_conflicts(make_project) -> None:
    root = make_project(
        {
            "requirements.txt": "Foo_Bar==1.0\n",
            "pyproject.toml": _pyproject("'foo-bar==1.0'"),
        }
    )
    ids = _ids(root)
    assert "RC200" not in ids
    assert "RC201" not in ids


# --------------------------------------------------------------------------- #
# 22. unbounded and mixed pinning
# --------------------------------------------------------------------------- #


def test_unbounded_runtime_dependency(make_project) -> None:
    root = make_project({"pyproject.toml": _pyproject("'requests', 'pydantic==2.6'")})
    finding = _finding(root, "RC203")

    assert finding.severity.name == "INFO"
    assert finding.confidence.name == "HIGH"
    assert finding.message.startswith("1 runtime dependency")
    assert "no version constraint" in finding.message


def test_mixed_pinning_strategy(make_project) -> None:
    root = make_project(
        {"pyproject.toml": _pyproject("'a==1.0', 'b==1.0', 'c==1.0', 'd', 'e', 'f>=1'")}
    )
    finding = _finding(root, "RC204")

    assert finding.severity.name == "INFO"
    assert "3 exact pin(s)" in finding.message
    assert "1 range(s)" in finding.message
    assert "2 without constraint" in finding.message


def test_consistent_pinning_is_not_reported(make_project) -> None:
    root = make_project({"pyproject.toml": _pyproject("'a==1.0', 'b==1.0', 'c==1.0'")})
    assert "RC204" not in _ids(root)


def test_small_dependency_lists_are_not_judged(make_project) -> None:
    root = make_project({"pyproject.toml": _pyproject("'a==1.0', 'b'")})
    assert "RC204" not in _ids(root)


# --------------------------------------------------------------------------- #
# 23. build requirements live in their own scope
# --------------------------------------------------------------------------- #


def test_build_requirements_do_not_conflict_with_runtime(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": (
                "[project]\n"
                "name = 'x'\n"
                "dependencies = ['setuptools==1.0']\n"
                "[build-system]\n"
                "requires = ['setuptools==2.0']\n"
            )
        }
    )
    assert not {"RC200", "RC201"} & _ids(root)
