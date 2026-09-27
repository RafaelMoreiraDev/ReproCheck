"""Tests for the isolated reproduction workflow.

No test in this module reaches the network. Projects are installed with an
in-tree PEP 517 backend (``requires = []``) and requirements are satisfied from
hand-written local wheels, so ``pip`` always runs with ``--no-index``.

The module deliberately performs few full runs: each one creates a virtual
environment, so related assertions are grouped into a single run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import inhouse_backend_files, make_wheel, requires_git, run_git
from reprocheck.cli import EXIT_ERROR, EXIT_FAIL, EXIT_PARTIAL, main
from reprocheck.reproduction import python_selector, runner
from reprocheck.reproduction.installer import (
    BASE_PIP_FLAGS,
    STRATEGY_PROJECT,
    detect_strategy,
    needs_network,
    venv_python,
)
from reprocheck.reproduction.models import ReproductionReport, snippet
from reprocheck.reproduction.pip_check import parse_conflicts, run_pip_check
from reprocheck.reproduction.runner import reproduce
from reprocheck.reproduction.workspace import cleanup, create_workspace
from reprocheck.scanner import collect_facts, scan


def _reproduce(root: Path, tmp_path: Path, **kwargs):
    return reproduce(root, base_dir=tmp_path / "workspaces", **kwargs)


def _ids(report) -> set[str]:
    return {finding.id for finding in report.reproduction_findings}


def _fingerprint(root: Path) -> dict[str, tuple[int, int]]:
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _current_minor() -> str:
    """The interpreter running the tests, as a ``.python-version`` value."""
    return f"{sys.version_info[0]}.{sys.version_info[1]}"


def _failing_backend(name: str) -> dict[str, str]:
    files = inhouse_backend_files(name)
    files["inhouse_backend.py"] = (
        "def build_wheel(*a, **k):\n    raise RuntimeError('boom')\n"
    )
    return files


def _venv_python(venv: Path) -> str:
    return venv_python(
        type("W", (), {"venv": venv})()  # minimal duck-typed workspace
    )


# --------------------------------------------------------------------------- #
# 1./2. the successful pipeline, in one run
# --------------------------------------------------------------------------- #


def test_successful_reproduction(make_project, tmp_path) -> None:
    root = make_project(
        {
            **inhouse_backend_files("rc-demo"),
            ".git/config": "[core]\n",
            ".venv/lib/x.py": "",
            "__pycache__/x.pyc": "",
            "build/artifact.txt": "",
            "dist/artifact.whl": "",
        }
    )

    report = _reproduce(root, tmp_path, keep_workspace=True)
    data = report.reproduction

    # 1. installable project, installed offline
    installation = data["installation"]
    assert installation["strategy"] == "project"
    assert installation["success"] is True
    assert installation["exit_code"] == 0
    assert installation["network_enabled"] is False
    assert "--no-index" in installation["command"]
    assert installation["duration_seconds"] >= 0

    # 2. paths and steps are recorded
    assert installation["cwd"] == data["source"]
    assert data["source"].endswith("source")
    assert data["venv"]["path"].endswith("venv")
    assert data["venv"]["created"] is True
    assert data["venv"]["python_version"]
    assert data["venv"]["initial_pip_version"]
    assert data["python"]["selected"]
    assert data["python"]["executable"]
    assert data["python"]["candidates"]
    assert data["completed_steps"] == [
        "static scan",
        "workspace created",
        "project copied",
        f"python selected: {data['python']['selected']}",
        "venv created",
        "strategy: project",
        "installation succeeded",
        "pip check completed",
        "installed version: 0.1.0",
    ]

    # 9. the installed version is read from the reproduced environment
    assert data["installed_distribution"]["name"] == "rc-demo"
    assert data["installed_distribution"]["version"] == "0.1.0"
    assert data["installed_distribution"]["looks_like_fallback"] is False

    # runtime checks are opt-in
    assert data["runtime_checks_enabled"] is False
    assert data["runtime_checks"]["enabled"] is False
    assert data["runtime_checks"]["imports"] == []
    assert data["runtime_checks"]["pytest_collection"]["available"] is False

    # 6. pip check is clean
    assert data["pip_check"]["ran"] is True
    assert data["pip_check"]["clean"] is True
    assert data["pip_check"]["conflicts"] == []
    assert "RC400" not in _ids(report)

    # 8. the original is untouched
    assert data["original_project_unchanged"] is True
    assert "RC406" not in _ids(report)

    # 9./10. the copy excludes environments, caches and git metadata
    source = Path(str(data["source"]))
    assert (source / "pyproject.toml").is_file()
    assert not (source / ".git").exists()
    assert not (source / ".venv").exists()
    assert not (source / "__pycache__").exists()
    assert not (source / "build").exists()
    assert not (source / "dist").exists()

    # 10. the venv lives outside the project
    venv = Path(str(data["venv"]["path"]))
    assert venv.is_dir()
    assert root.resolve() not in venv.parents
    assert tmp_path.resolve() in venv.parents
    assert not (root / "venv").exists()

    # 12. --keep-workspace preserves the tree
    assert data["workspace_kept"] is True
    assert Path(str(data["workspace"])).is_dir()

    # 15. logs live in the workspace
    logs = Path(str(data["workspace"])) / "logs"
    for name in (
        "install.stdout.log",
        "install.stderr.log",
        "pip-check.stdout.log",
        "venv.stdout.log",
        "pip-version.stdout.log",
    ):
        assert (logs / name).is_file(), name
    assert str(installation["stderr_path"]).startswith(str(logs))

    # 16. the JSON stays small
    payload = json.dumps(report.to_dict())
    assert len(payload) < 200_000
    assert len(installation["stdout_snippet"]) <= 2000

    assert report.reproduction_findings == []
    assert any(item.id.startswith("RC0") for item in report.findings)
    assert set(report.to_dict()["reproduction"]) >= {
        "attempted",
        "workspace",
        "python",
        "installation",
        "pip_check",
        "original_project_unchanged",
    }


# --------------------------------------------------------------------------- #
# 11. cleanup policy
# --------------------------------------------------------------------------- #


def test_workspace_is_removed_after_success(make_project, tmp_path) -> None:
    root = make_project(inhouse_backend_files("rc-demo"))

    report = _reproduce(root, tmp_path)

    assert report.reproduction["workspace"] is None
    assert report.reproduction["workspace_kept"] is False
    assert not list((tmp_path / "workspaces" / "reprocheck").glob("*"))


def test_workspace_is_preserved_after_a_failure(make_project, tmp_path) -> None:
    root = make_project(_failing_backend("rc-broken"))

    report = _reproduce(root, tmp_path)
    workspace = report.reproduction["workspace"]

    assert report.reproduction["workspace_kept"] is True
    assert workspace is not None
    assert Path(str(workspace)).is_dir()
    assert (Path(str(workspace)) / "logs" / "install.stderr.log").is_file()


def test_workspace_layout_and_cleanup_are_stable(tmp_path) -> None:
    workspace = create_workspace(tmp_path)

    assert workspace.source.name == "source"
    assert workspace.venv.name == "venv"
    assert workspace.logs.name == "logs"
    assert workspace.artifacts.name == "artifacts"
    assert workspace.report.name == "report.json"
    assert workspace.root.parent.name == "reprocheck"

    cleanup(workspace)
    cleanup(workspace)
    assert not workspace.root.exists()


# --------------------------------------------------------------------------- #
# 2. requirements strategy
# --------------------------------------------------------------------------- #


def test_requirements_strategy_without_a_local_index(make_project, tmp_path) -> None:
    make_wheel(tmp_path / "wheelhouse", "localdep", "1.2")
    root = make_project(
        {
            "requirements.txt": "localdep==1.2\n",
            ".python-version": _current_minor(),
        }
    )

    report = _reproduce(root, tmp_path)
    installation = report.reproduction["installation"]

    assert installation["strategy"] == "requirements"
    assert "-r" in installation["command"]
    # The wheel is unreachable without an explicit local index: the failure is
    # reported, never hidden.
    assert installation["success"] is False
    assert {"RC401", "RC404"} <= _ids(report)


def test_requirements_strategy_succeeds_from_a_local_wheel(
    make_project, tmp_path
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    make_wheel(wheelhouse, "localdep", "1.2")
    root = make_project(
        {
            "requirements.txt": (
                f'--no-index\n--find-links "{wheelhouse}"\nlocaldep==1.2\n'
            ),
            ".python-version": _current_minor(),
        }
    )

    report = _reproduce(root, tmp_path)

    assert report.reproduction["installation"]["success"] is True
    assert report.reproduction["pip_check"]["clean"] is True
    assert not _ids(report)


# --------------------------------------------------------------------------- #
# 3. no strategy
# --------------------------------------------------------------------------- #


def test_no_installation_strategy(make_project, tmp_path) -> None:
    root = make_project(
        {
            "a.py": "VALUE = 1\n",
            "README.md": "# x\n",
            ".python-version": _current_minor(),
        }
    )

    report = _reproduce(root, tmp_path)

    assert report.reproduction["installation"] is None
    assert _ids(report) == {"RC403"}
    assert "no installation strategy" in report.reproduction["completed_steps"]
    finding = report.reproduction_findings[0]
    assert finding.severity.name == "WARNING"
    assert finding.confidence.name == "HIGH"


# --------------------------------------------------------------------------- #
# 4./5. Python selection
# --------------------------------------------------------------------------- #


def test_required_python_missing(make_project, tmp_path) -> None:
    root = make_project(inhouse_backend_files("rc-demo", requires_python="==3.99"))

    report = _reproduce(root, tmp_path)

    assert "RC402" in _ids(report)
    assert report.reproduction["venv"]["created"] is False
    assert report.reproduction["installation"] is None
    finding = next(item for item in report.reproduction_findings if item.id == "RC402")
    assert finding.severity.name == "ERROR"
    assert "3.99" in finding.message


def test_python_selector_prefers_python_version_file(monkeypatch) -> None:
    from reprocheck.facts import Facts
    from reprocheck.models import ProjectScan, PythonRequirement
    from reprocheck.reproduction.models import InterpreterCandidate

    monkeypatch.setattr(
        python_selector,
        "discover_interpreters",
        lambda: [
            InterpreterCandidate("C:/py311/python.exe", "3.11.4", "test"),
            InterpreterCandidate("C:/py312/python.exe", "3.12.1", "test"),
        ],
    )
    facts = Facts(
        project=ProjectScan(name="x", path="C:/x"),
        python_requirements=[
            PythonRequirement(".python-version", "3.11.4"),
            PythonRequirement("pyproject.toml [project.requires-python]", ">=3.10"),
        ],
    )

    selection = python_selector.select_python(facts)

    assert selection.selected == "3.11.4"
    assert ".python-version" in selection.reason


def test_python_selector_treats_a_partial_pin_as_a_series(monkeypatch) -> None:
    from reprocheck.facts import Facts
    from reprocheck.models import ProjectScan, PythonRequirement
    from reprocheck.reproduction.models import InterpreterCandidate

    monkeypatch.setattr(
        python_selector,
        "discover_interpreters",
        lambda: [InterpreterCandidate("C:/py314/python.exe", "3.14.7", "test")],
    )
    facts = Facts(
        project=ProjectScan(name="x", path="C:/x"),
        python_requirements=[PythonRequirement(".python-version", "3.14")],
    )

    assert python_selector.select_python(facts).selected == "3.14.7"


def test_python_selector_refuses_ambiguity() -> None:
    from reprocheck.facts import Facts
    from reprocheck.models import ProjectScan

    selection = python_selector.select_python(
        Facts(project=ProjectScan(name="x", path="C:/x"))
    )

    if len(selection.candidates) == 1:
        assert selection.selected is not None
    else:
        assert selection.selected is None
        assert "deterministic" in selection.reason


def test_python_selector_refuses_conflicting_sources(monkeypatch) -> None:
    from reprocheck.facts import Facts
    from reprocheck.models import ProjectScan, PythonRequirement
    from reprocheck.reproduction.models import InterpreterCandidate

    monkeypatch.setattr(
        python_selector,
        "discover_interpreters",
        lambda: [
            InterpreterCandidate("C:/py311/python.exe", "3.11.4", "test"),
            InterpreterCandidate("C:/py312/python.exe", "3.12.1", "test"),
        ],
    )
    facts = Facts(
        project=ProjectScan(name="x", path="C:/x"),
        python_requirements=[
            PythonRequirement(".python-version", "3.10.0"),
            PythonRequirement("pyproject.toml [project.requires-python]", ">=3.11"),
        ],
    )

    selection = python_selector.select_python(facts)

    assert selection.selected is None
    assert "disagree" in selection.reason


def test_python_selector_uses_lowest_satisfying(monkeypatch) -> None:
    from reprocheck.facts import Facts
    from reprocheck.models import ProjectScan, PythonRequirement
    from reprocheck.reproduction.models import InterpreterCandidate

    monkeypatch.setattr(
        python_selector,
        "discover_interpreters",
        lambda: [
            InterpreterCandidate("C:/py312/python.exe", "3.12.1", "test"),
            InterpreterCandidate("C:/py314/python.exe", "3.14.0", "test"),
            InterpreterCandidate("C:/py311/python.exe", "3.11.9", "test"),
        ],
    )
    facts = Facts(
        project=ProjectScan(name="x", path="C:/x"),
        python_requirements=[
            PythonRequirement("pyproject.toml [project.requires-python]", ">=3.11")
        ],
    )

    selection = python_selector.select_python(facts)

    assert selection.selected == "3.11.9"
    assert "lowest installed version" in selection.reason


# --------------------------------------------------------------------------- #
# 6. failing installation
# --------------------------------------------------------------------------- #


def test_failing_backend_is_reported(make_project, tmp_path) -> None:
    root = make_project(_failing_backend("rc-broken"))

    report = _reproduce(root, tmp_path, keep_workspace=True)
    installation = report.reproduction["installation"]
    finding = next(item for item in report.reproduction_findings if item.id == "RC401")

    assert installation["success"] is False
    assert installation["exit_code"] not in (0, None)
    assert finding.severity.name == "ERROR"
    assert finding.confidence.name == "HIGH"
    assert "boom" in finding.message or "boom" in str(finding.evidence)
    assert "command:" in str(finding.evidence)
    assert Path(str(installation["stderr_path"])).is_file()


# --------------------------------------------------------------------------- #
# 7. pip check
# --------------------------------------------------------------------------- #


def test_pip_check_parses_conflicts() -> None:
    output = (
        "broken 1.0 requires missingdep 2.0, which is not installed.\n"
        "other 3.1 has requirement conflictdep 1.0, but you have conflictdep 2.0.\n"
    )
    conflicts = parse_conflicts(output)

    assert len(conflicts) == 2
    assert conflicts[0] == "broken requires missingdep 2.0, which is not installed"
    assert conflicts[1] == (
        "other requires conflictdep 1.0, but conflictdep 2.0 is installed"
    )


def test_pip_check_reports_a_real_conflict(tmp_path) -> None:
    """Two wheels with conflicting requirements, installed without resolution."""
    wheelhouse = tmp_path / "wheelhouse"
    make_wheel(wheelhouse, "sharedlib", "1.0")
    make_wheel(wheelhouse, "alpha", "1.0", requires=["sharedlib==1.0"])
    make_wheel(wheelhouse, "beta", "1.0", requires=["sharedlib==2.0"])

    workspace = create_workspace(tmp_path / "workspaces")
    subprocess.run(
        [sys.executable, "-m", "venv", str(workspace.venv)],
        check=True,
        capture_output=True,
    )
    python = _venv_python(workspace.venv)
    for name in ("sharedlib", "alpha", "beta"):
        subprocess.run(
            [
                python,
                "-m",
                "pip",
                "install",
                "--no-input",
                "--disable-pip-version-check",
                "--no-index",
                "--no-deps",
                str(wheelhouse / f"{name}-1.0-py3-none-any.whl"),
            ],
            check=True,
            capture_output=True,
        )

    result = run_pip_check(workspace, dict(os.environ))

    assert result.ran is True
    assert result.clean is False
    assert result.exit_code == 1
    assert result.conflict_count == 1
    assert "sharedlib" in result.conflicts[0]
    assert "beta" in result.conflicts[0]


# --------------------------------------------------------------------------- #
# 8. read-only guarantee
# --------------------------------------------------------------------------- #


@requires_git
def test_original_git_project_is_untouched(make_project, tmp_path) -> None:
    root = make_project(inhouse_backend_files("rc-demo"))
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.email", "test@example.com")
    run_git(root, "config", "user.name", "ReproCheck Test")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "initial")
    before = _fingerprint(root)
    head = run_git(root, "rev-parse", "HEAD").stdout

    report = reproduce(root, base_dir=tmp_path / "workspaces")

    assert _fingerprint(root) == before
    assert run_git(root, "rev-parse", "HEAD").stdout == head
    assert run_git(root, "status", "--porcelain").stdout == ""
    assert report.reproduction["original_project_unchanged"] is True
    assert report.reproduction["integrity"]["git_head_before"] == head.strip()
    assert report.reproduction["integrity"]["tracked_files"] > 0
    assert "RC406" not in _ids(report)


# --------------------------------------------------------------------------- #
# 13. network policy
# --------------------------------------------------------------------------- #


def test_network_flag_builds_an_index_command(tmp_path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "1"\n', encoding="utf-8"
    )
    facts = collect_facts(root)
    workspace = create_workspace(tmp_path / "workspaces")
    (workspace.source / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "1"\n', encoding="utf-8"
    )

    strategy = detect_strategy(facts, workspace)

    assert strategy.name == STRATEGY_PROJECT
    assert "--no-index" not in strategy.command
    assert all(flag in strategy.command for flag in BASE_PIP_FLAGS)


def test_needs_network_only_reads_the_error_text() -> None:
    from reprocheck.reproduction.models import InstallationAttempt

    attempt = InstallationAttempt(
        strategy="project",
        command=("python", "-m", "pip", "install"),
        cwd="C:/x",
        source="C:/x",
        venv="C:/x/venv",
        network_enabled=False,
        exit_code=1,
        stderr_snippet=(
            "ERROR: Could not find a version that satisfies the requirement"
        ),
    )
    unrelated = InstallationAttempt(
        strategy="project",
        command=("python", "-m", "pip", "install"),
        cwd="C:/x",
        source="C:/x",
        venv="C:/x/venv",
        network_enabled=False,
        exit_code=1,
        stderr_snippet="ERROR: backend exploded",
    )

    assert needs_network(attempt) is True
    assert needs_network(unrelated) is False


# --------------------------------------------------------------------------- #
# 14. CLI
# --------------------------------------------------------------------------- #


def test_cli_reproduce(make_project, tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(runner, "DEFAULT_BASE_DIR", tmp_path / "cli-workspaces")
    root = make_project(inhouse_backend_files("rc-demo"))
    destination = tmp_path / "report.json"

    code = main(["reproduce", str(root), "--json", str(destination), "--verbose"])
    output = capsys.readouterr().out

    # Runtime checks were not requested, so the verdict is PARTIAL (exit 1).
    assert code == EXIT_PARTIAL
    assert destination.is_file()
    assert "Reproduction" in output
    assert "original project unchanged: yes" in output
    assert "pip check" in output
    assert "steps:" in output
    assert "Verdict: PARTIAL" in output


def test_cli_reproduce_error_exit_code(make_project, tmp_path, monkeypatch) -> None:
    # The workspace of a failed attempt is preserved on purpose, so the CLI
    # tests point it at the pytest temp directory instead of the real one.
    monkeypatch.setattr(runner, "DEFAULT_BASE_DIR", tmp_path / "cli-workspaces")
    root = make_project(inhouse_backend_files("rc-demo", requires_python="==3.99"))

    code = main(["reproduce", str(root), "--json", str(tmp_path / "r.json")])

    assert code == EXIT_FAIL


def test_cli_reproduce_missing_path(tmp_path, capsys) -> None:
    code = main(
        ["reproduce", str(tmp_path / "nope"), "--json", str(tmp_path / "r.json")]
    )

    assert code == EXIT_ERROR
    assert "error" in capsys.readouterr().err


def test_scan_report_has_no_reproduction_section(make_project) -> None:
    root = make_project(inhouse_backend_files("rc-demo"))

    assert "reproduction" not in scan(root).to_dict()


# --------------------------------------------------------------------------- #
# 16. report size helpers
# --------------------------------------------------------------------------- #


def test_snippet_keeps_the_tail() -> None:
    assert snippet("short") == "short"
    trimmed = snippet("a" * 5000, 100)

    assert trimmed.startswith("...")
    assert len(trimmed) == 100


def test_reproduction_report_defaults() -> None:
    empty = ReproductionReport()

    assert empty.to_dict()["attempted"] is True
    assert empty.to_dict()["original_project_unchanged"] is True
