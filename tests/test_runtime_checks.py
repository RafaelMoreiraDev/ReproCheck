"""Tests for the optional runtime checks and the Git-derived version check.

Runtime checks execute code, so these tests use the isolated workspace and
local wheels only. Nothing here reaches the network.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from conftest import inhouse_backend_files, make_wheel
from reprocheck.checks.versioning import check_git_derived_version
from reprocheck.facts import Facts
from reprocheck.models import ProjectScan
from reprocheck.reproduction import runtime
from reprocheck.reproduction.runner import reproduce
from reprocheck.reproduction.workspace import cleanup, create_workspace

SHA = "0123456789abcdef0123456789abcdef01234567"


def _reproduce(root: Path, tmp_path: Path, **kwargs):
    return reproduce(root, base_dir=tmp_path / "workspaces", **kwargs)


def _ids(report) -> set[str]:
    return {finding.id for finding in report.reproduction_findings}


def _current_minor() -> str:
    return f"{sys.version_info[0]}.{sys.version_info[1]}"


def _project(name: str, payload: str = "VALUE = 1\n", **extra) -> dict[str, str]:
    files = inhouse_backend_files(name, **extra)
    module = name.replace("-", "_")
    files[f"{module}.py"] = payload
    return files


def _rc150(dynamic: tuple[str, ...], providers: tuple[str, ...]):
    return check_git_derived_version(
        Facts(
            project=ProjectScan(name="x", path="C:/x"),
            distribution_name="x",
            dynamic_fields=dynamic,
            version_providers=providers,
        )
    )


# --------------------------------------------------------------------------- #
# 1./2. opt-in
# --------------------------------------------------------------------------- #


def test_runtime_checks_are_disabled_by_default(make_project, tmp_path) -> None:
    root = make_project(_project("rc-quiet"))

    report = _reproduce(root, tmp_path)
    data = report.reproduction

    assert data["runtime_checks_enabled"] is False
    assert data["runtime_checks"]["enabled"] is False
    assert data["runtime_checks"]["imports"] == []
    assert data["runtime_checks"]["import_discovery"] is None
    assert data["runtime_checks"]["pytest_collection"]["ran"] is False
    assert not any("import" in step for step in data["completed_steps"])


def test_runtime_checks_flag_enables_the_steps(make_project, tmp_path) -> None:
    root = make_project(_project("rc-loud"))

    report = _reproduce(root, tmp_path, runtime_checks=True)
    data = report.reproduction

    assert data["runtime_checks_enabled"] is True
    assert [item["module"] for item in data["runtime_checks"]["imports"]] == ["rc_loud"]
    assert data["runtime_checks"]["imports"][0]["imported"] is True
    assert any("import smoke test" in step for step in data["completed_steps"])
    assert any("pytest collection" in step for step in data["completed_steps"])


# --------------------------------------------------------------------------- #
# 3./4. import results
# --------------------------------------------------------------------------- #


def test_importable_package(make_project, tmp_path) -> None:
    root = make_project(_project("rc-good"))

    report = _reproduce(root, tmp_path, runtime_checks=True)
    check = report.reproduction["runtime_checks"]["imports"][0]

    assert check["module"] == "rc_good"
    assert check["imported"] is True
    assert check["exit_code"] == 0
    assert check["timed_out"] is False
    assert "RC500" not in _ids(report)
    assert "RC502" not in _ids(report)


def test_broken_import(make_project, tmp_path) -> None:
    root = make_project(
        _project("rc-broken", payload="raise RuntimeError('boom at import')\n")
    )

    report = _reproduce(root, tmp_path, runtime_checks=True, keep_workspace=True)
    finding = next(item for item in report.reproduction_findings if item.id == "RC500")
    check = report.reproduction["runtime_checks"]["imports"][0]

    assert check["imported"] is False
    assert check["exit_code"] not in (0, None)
    assert finding.severity.name == "ERROR"
    assert finding.confidence.name == "HIGH"
    assert "rc_broken" in finding.message
    assert "boom at import" in str(finding.evidence)
    assert Path(str(check["stderr_path"])).is_file()


def test_import_timeout_does_not_leave_orphans(monkeypatch, tmp_path) -> None:
    """A hanging import is terminated, and the tree does not survive it."""
    workspace = create_workspace(tmp_path)
    # The child runs with the workspace copy as its working directory, so the
    # module under test is importable from there.
    (workspace.source / "slowpkg.py").write_text(
        "import time\ntime.sleep(120)\n", encoding="utf-8"
    )
    monkeypatch.setattr(runtime, "venv_python", lambda _ws: sys.executable)
    monkeypatch.setattr(runtime, "IMPORT_TIMEOUT", 2)

    started = time.monotonic()
    checks = runtime.run_import_checks(workspace, ("slowpkg",), dict(os.environ))
    elapsed = time.monotonic() - started

    assert elapsed < 30, "the import process tree was not terminated in time"
    assert checks[0].timed_out is True
    assert checks[0].imported is False
    assert checks[0].exit_code == 124
    assert "timed out" in checks[0].stderr_snippet
    cleanup(workspace)


def test_fast_imports_are_not_affected(monkeypatch, tmp_path) -> None:
    workspace = create_workspace(tmp_path)
    monkeypatch.setattr(runtime, "venv_python", lambda _ws: sys.executable)
    monkeypatch.setattr(runtime, "IMPORT_TIMEOUT", 30)

    checks = runtime.run_import_checks(workspace, ("json", "sys"), dict(os.environ))

    assert [item.module for item in checks] == ["json", "sys"]
    assert all(item.imported for item in checks)
    assert not any(item.timed_out for item in checks)
    cleanup(workspace)


# --------------------------------------------------------------------------- #
# 5./6. distribution name versus import module
# --------------------------------------------------------------------------- #


def test_distribution_name_differs_from_import_module(make_project, tmp_path) -> None:
    root = make_project(_project("totally-different-name"))

    report = _reproduce(root, tmp_path, runtime_checks=True)
    modules = [
        item["module"] for item in report.reproduction["runtime_checks"]["imports"]
    ]

    assert modules == ["totally_different_name"]
    assert (
        report.reproduction["installed_distribution"]["name"]
        == "totally-different-name"
    )


def test_multiple_top_level_modules(make_project, tmp_path) -> None:
    files = inhouse_backend_files("rc-multi")
    files["rc_multi.py"] = "VALUE = 1\n"
    files["rc_extra.py"] = "VALUE = 2\n"
    files["rc_helper.py"] = "VALUE = 3\n"
    root = make_project(files)

    report = _reproduce(root, tmp_path, runtime_checks=True)
    modules = [
        item["module"] for item in report.reproduction["runtime_checks"]["imports"]
    ]

    assert modules == ["rc_extra", "rc_helper", "rc_multi"]
    assert all(
        item["imported"] for item in report.reproduction["runtime_checks"]["imports"]
    )


def test_documentation_directories_are_not_candidates(make_project, tmp_path) -> None:
    files = inhouse_backend_files("rc-doc")
    files["rc_doc.py"] = "VALUE = 1\n"
    files["tests/__init__.py"] = ""
    files["docs/__init__.py"] = ""
    root = make_project(files)

    report = _reproduce(root, tmp_path, runtime_checks=True)
    modules = [
        item["module"] for item in report.reproduction["runtime_checks"]["imports"]
    ]

    assert modules == ["rc_doc"]


def test_indeterminable_import_target_is_not_an_error(make_project, tmp_path) -> None:
    """A distribution with no importable top-level module is reported, not invented."""
    files = inhouse_backend_files("rc-payload")
    files.pop("rc_payload.py")
    files["data/__init__.py"] = "VALUE = 1\n"
    root = make_project(files, name="rc-payload-only-data")

    report = _reproduce(root, tmp_path, runtime_checks=True)
    runtime_checks = report.reproduction["runtime_checks"]

    assert runtime_checks["imports"] == []
    assert "no unambiguous top-level module" in str(runtime_checks["import_discovery"])
    assert not {"RC500", "RC502"} & _ids(report)


def test_project_without_distribution_name_skips_imports(tmp_path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    make_wheel(wheelhouse, "plaindep", "1.0")
    root = tmp_path / "no-name"
    root.mkdir()
    (root / "requirements.txt").write_text(
        f'--no-index\n--find-links "{wheelhouse}"\nplaindep==1.0\n', encoding="utf-8"
    )
    (root / ".python-version").write_text(_current_minor(), encoding="utf-8")
    (root / "a.py").write_text("VALUE = 1\n", encoding="utf-8")

    report = _reproduce(root, tmp_path, runtime_checks=True)

    assert report.reproduction["installation"]["success"] is True
    assert report.reproduction["runtime_checks"]["imports"] == []
    assert "no distribution name is declared" in str(
        report.reproduction["runtime_checks"]["import_discovery"]
    )


# --------------------------------------------------------------------------- #
# 7./8. pytest collection
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 7./8. pytest collection
# --------------------------------------------------------------------------- #

#: The reproduced venv needs a real pytest. This suite already runs inside
#: one, so the same installation is exposed to the workspace venv through
#: PYTHONPATH: nothing is downloaded and no index is contacted.
_HOST_SITE_PACKAGES = str(Path(pytest.__file__).resolve().parent.parent)


def _project_with_pytest(tmp_path, name, test_body, conftest=None):
    files = inhouse_backend_files(name)
    files[f"{name.replace('-', '_')}.py"] = "VALUE = 1\n"
    files["tests/__init__.py"] = ""
    files["tests/test_ok.py"] = test_body
    files["pytest.ini"] = "[pytest]\ntestpaths = tests\n"
    if conftest is not None:
        files["conftest.py"] = conftest
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def test_pytest_collection_succeeds(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", _HOST_SITE_PACKAGES)
    root = _project_with_pytest(
        tmp_path, "rc-collected", "def test_ok():\n    assert True\n"
    )

    report = _reproduce(root, tmp_path, runtime_checks=True)
    collection = report.reproduction["runtime_checks"]["pytest_collection"]

    assert collection["available"] is True
    assert collection["ran"] is True
    assert collection["success"] is True, collection
    assert collection["collected"] == 1
    assert "RC501" not in _ids(report)


def test_pytest_collection_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", _HOST_SITE_PACKAGES)
    root = _project_with_pytest(
        tmp_path,
        "rc-broken-test",
        "import nonexistent_module_xyz\n\ndef test_ok():\n    assert True\n",
    )

    report = _reproduce(root, tmp_path, runtime_checks=True)
    collection = report.reproduction["runtime_checks"]["pytest_collection"]
    finding = next(item for item in report.reproduction_findings if item.id == "RC501")

    assert collection["success"] is False
    assert collection["exit_code"] not in (0, None)
    assert finding.severity.name == "WARNING"
    assert finding.confidence.name == "HIGH"
    assert "were not executed" in finding.message


def test_pytest_absent_is_skipped(make_project, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    root = make_project(_project("rc-no-pytest"))

    report = _reproduce(root, tmp_path, runtime_checks=True)
    collection = report.reproduction["runtime_checks"]["pytest_collection"]

    assert collection["available"] is False
    assert collection["ran"] is False
    assert "not installed" in str(collection["reason"])
    assert "RC501" not in _ids(report)


def test_collection_runs_conftest_inside_the_copy(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", _HOST_SITE_PACKAGES)
    marker = tmp_path / "conftest-ran.txt"
    root = _project_with_pytest(
        tmp_path,
        "rc-conftest",
        "def test_ok():\n    assert True\n",
        conftest=(
            "from pathlib import Path\n"
            f"Path(r'{marker}').write_text('executed', encoding='utf-8')\n"
        ),
    )

    report = _reproduce(root, tmp_path, runtime_checks=True)
    collection = report.reproduction["runtime_checks"]["pytest_collection"]

    assert collection["ran"] is True
    assert marker.is_file(), collection
    # The original project never received the marker.
    assert not (root / "conftest-ran.txt").exists()
    assert report.reproduction["original_project_unchanged"] is True


def test_tests_are_not_executed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", _HOST_SITE_PACKAGES)
    marker = tmp_path / "test-ran.txt"
    root = _project_with_pytest(
        tmp_path,
        "rc-not-executed",
        "from pathlib import Path\n"
        "\n"
        f"MARKER = Path(r'{marker}')\n"
        "\n"
        "def test_ok():\n"
        "    MARKER.write_text('executed', encoding='utf-8')\n",
    )

    report = _reproduce(root, tmp_path, runtime_checks=True)

    assert report.reproduction["runtime_checks"]["pytest_collection"]["ran"] is True
    assert report.reproduction["runtime_checks"]["pytest_collection"]["success"], (
        "collection must have imported the test module: "
        f"{report.reproduction['runtime_checks']['pytest_collection']}"
    )
    assert not marker.exists(), "test functions must not be executed"


# --------------------------------------------------------------------------- #
# 9./10. installed distribution
# --------------------------------------------------------------------------- #


def test_installed_version_is_captured(make_project, tmp_path) -> None:
    root = make_project(inhouse_backend_files("rc-versioned", version="2.5.1"))

    report = _reproduce(root, tmp_path)
    installed = report.reproduction["installed_distribution"]

    assert installed["name"] == "rc-versioned"
    assert installed["version"] == "2.5.1"
    assert installed["looks_like_fallback"] is False
    assert installed["note"] is None


def test_original_project_is_untouched_by_runtime_checks(
    make_project, tmp_path
) -> None:
    files = _project("rc-untouched")
    root = make_project(files)
    before = {
        path.relative_to(root).as_posix(): path.stat().st_mtime_ns
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }

    report = _reproduce(root, tmp_path, runtime_checks=True)

    after = {
        path.relative_to(root).as_posix(): path.stat().st_mtime_ns
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    assert after == before
    assert report.reproduction["original_project_unchanged"] is True
    assert "RC406" not in _ids(report)


def test_runtime_section_shape(make_project, tmp_path) -> None:
    root = make_project(_project("rc-shape"))

    report = _reproduce(root, tmp_path, runtime_checks=True)
    section = report.to_dict()["reproduction"]["runtime_checks"]

    assert set(section) == {
        "enabled",
        "import_discovery",
        "imports",
        "pytest_collection",
    }
    assert set(section["imports"][0]) == {
        "module",
        "imported",
        "exit_code",
        "duration_seconds",
        "timed_out",
        "stderr_snippet",
        "stdout_path",
        "stderr_path",
        "error",
    }
    payload = json.dumps(report.to_dict())
    assert len(payload) < 200_000


# --------------------------------------------------------------------------- #
# 11./12. RC150: Git-derived version
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "provider",
    [
        "setuptools-git-versioning",
        "setuptools_scm",
        "hatch-vcs",
        "poetry-dynamic-versioning",
    ],
)
def test_rc150_for_each_known_provider(provider: str) -> None:
    finding = _rc150(("version",), (provider,))

    assert finding is not None
    assert finding.id == "RC150"
    assert finding.severity.name == "WARNING"
    assert finding.confidence.name == "HIGH"
    assert provider in finding.message
    assert "will not fail" not in finding.message


def test_rc150_from_a_project_declaration(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": (
                "[build-system]\n"
                'requires = ["setuptools>=67", "setuptools-git-versioning>=2.0,<3"]\n'
                'build-backend = "setuptools.build_meta"\n'
                "\n[project]\n"
                'name = "rc-git-version"\n'
                'dynamic = ["version"]\n'
            ),
            "rc_git_version.py": "VALUE = 1\n",
        }
    )

    from reprocheck.scanner import scan

    ids = {item.id for item in scan(root).findings}

    assert "RC150" in ids


def test_rc150_with_setuptools_scm_configuration(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": (
                "[build-system]\n"
                'requires = ["setuptools>=67", "setuptools_scm>=8"]\n'
                'build-backend = "setuptools.build_meta"\n'
                "\n[project]\n"
                'name = "rc-scm"\n'
                'dynamic = ["version"]\n'
                '\n[tool.setuptools_scm]\nwrite_to = "version.py"\n'
            )
        }
    )

    from reprocheck.scanner import scan

    assert "RC150" in {item.id for item in scan(root).findings}


def test_no_rc150_for_a_static_version(make_project) -> None:
    root = make_project(inhouse_backend_files("rc-static", version="1.0.0"))

    from reprocheck.scanner import scan

    assert "RC150" not in {item.id for item in scan(root).findings}


def test_no_rc150_for_a_non_git_dynamic_version(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": (
                "[build-system]\n"
                'requires = ["hatchling"]\n'
                'build-backend = "hatchling.build"\n'
                "\n[project]\n"
                'name = "rc-hatch"\n'
                'dynamic = ["version"]\n'
                '\n[tool.hatch.version]\npath = "src/rc_hatch/VERSION.txt"\n'
            )
        }
    )

    from reprocheck.scanner import scan

    assert "RC150" not in {item.id for item in scan(root).findings}


def test_no_rc150_when_version_is_not_dynamic(make_project) -> None:
    root = make_project(
        {
            "pyproject.toml": (
                "[build-system]\n"
                'requires = ["setuptools-git-versioning"]\n'
                "\n[project]\n"
                'name = "rc-mixed"\n'
                'version = "1.0"\n'
                'dynamic = ["classifiers"]\n'
            )
        }
    )

    from reprocheck.scanner import scan

    assert "RC150" not in {item.id for item in scan(root).findings}


def test_rc150_without_providers_or_dynamic_field() -> None:
    assert _rc150(("version",), ()) is None
    assert _rc150(("classifiers",), ("setuptools_scm",)) is None
    assert _rc150((), ()) is None


def test_fallback_version_is_flagged_as_suspicious_not_wrong(
    make_project, tmp_path
) -> None:
    root = make_project(inhouse_backend_files("rc-fallback", version="0.0.1"))

    report = _reproduce(root, tmp_path, runtime_checks=True)
    installed = report.reproduction["installed_distribution"]

    assert installed["version"] == "0.0.1"
    assert installed["looks_like_fallback"] is True
    assert "fallback" in str(installed["note"])
    assert "RC150" not in _ids(report)
