"""The reproduction pipeline.

::

    static scan
      -> isolated workspace (copy of the project)
      -> Python selection
      -> virtual environment
      -> installation
      -> pip check
      -> integrity verification of the original project

Every step is recorded, including the ones that did not run. The original
project is only ever read.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from reprocheck import __version__
from reprocheck.checks.reproduction import check_reproduction
from reprocheck.checks.verdict import compute_verdict
from reprocheck.facts import Facts
from reprocheck.models import ScanReport
from reprocheck.reproduction import conda, pip_check, python_selector, runtime
from reprocheck.reproduction import fingerprint as integrity
from reprocheck.reproduction import installer as installation
from reprocheck.reproduction import workspace as ws
from reprocheck.reproduction.models import (
    ReproductionReport,
    VenvInfo,
    snippet,
)
from reprocheck.scanner import build_report, collect_facts

DEFAULT_BASE_DIR: Path | None = None


class ReproductionError(Exception):
    """Raised when the reproduction cannot start at all."""


def reproduce(
    project: str | Path,
    *,
    network: bool = False,
    keep_workspace: bool = False,
    runtime_checks: bool = False,
    base_dir: Path | None = None,
    timeout: int = ws.DEFAULT_TIMEOUT,
    conda: bool = False,
    conda_env: str | None = None,
    conda_manager: str | None = None,
) -> ScanReport:
    """Attempt a controlled reproduction of ``project``.

    ``runtime_checks`` is opt-in: it imports the installed package and collects
    tests, which executes project code. ``conda`` is opt-in in the same way and
    for a stronger reason: it runs a third-party package manager, which
    downloads and executes packages. The original project is never modified.

    Without ``conda`` the attempt is exactly the pip pipeline it has always
    been. The two strategies are never merged: a report says which one produced
    it.
    """
    origin = _resolve(project)
    facts = collect_facts(origin)
    before = integrity.capture(origin)
    report = build_report(facts)

    workspace = ws.create_workspace(base_dir or DEFAULT_BASE_DIR)
    ws.copy_project(origin, workspace)

    reproduction = ReproductionReport(
        attempted=True,
        workspace=str(workspace.root),
        source=str(workspace.source),
        original_project=str(origin),
        network_enabled=network,
        runtime_checks_enabled=runtime_checks,
    )
    steps: list[str] = ["static scan", "workspace created", "project copied"]
    env = _subprocess_env(network=network)

    if conda:
        env = _conda_subprocess_env()
        return _reproduce_conda(
            report,
            reproduction,
            workspace,
            before,
            origin,
            steps,
            keep_workspace,
            facts,
            env=env,
            network=network,
            environment_file=conda_env,
            manager_name=conda_manager,
            runtime_checks=runtime_checks,
            timeout=timeout,
        )

    return _reproduce_pip(
        report,
        reproduction,
        workspace,
        before,
        origin,
        steps,
        keep_workspace,
        facts,
        env=env,
        network=network,
        runtime_checks=runtime_checks,
        timeout=timeout,
    )


def _reproduce_pip(
    report: ScanReport,
    reproduction: ReproductionReport,
    workspace: ws.Workspace,
    before: integrity.Fingerprint,
    origin: Path,
    steps: list[str],
    keep_workspace: bool,
    facts: Facts,
    *,
    env: dict[str, str],
    network: bool,
    runtime_checks: bool,
    timeout: int,
) -> ScanReport:
    """The pip pipeline, unchanged by the existence of the Conda one."""
    selection = python_selector.select_python(facts)
    reproduction.python = selection
    if selection.selected is None or selection.executable is None:
        steps.append("python selection refused")
        return _finish(
            report, reproduction, workspace, before, origin, steps, keep_workspace
        )
    steps.append(f"python selected: {selection.selected}")

    venv_result = _create_venv(selection.executable, workspace, env, timeout)
    reproduction.venv = venv_result
    if not venv_result.created:
        steps.append("venv creation failed")
        return _finish(
            report, reproduction, workspace, before, origin, steps, keep_workspace
        )
    steps.append("venv created")

    strategy = installation.detect_strategy(facts, workspace)
    if strategy.name == installation.STRATEGY_NONE:
        steps.append("no installation strategy")
        return _finish(
            report, reproduction, workspace, before, origin, steps, keep_workspace
        )
    steps.append(f"strategy: {strategy.name}")

    attempt = installation.install(
        strategy, workspace, original=origin, network=network, env=env
    )
    reproduction.installation = attempt
    if not attempt.success:
        steps.append("installation failed")
        return _finish(
            report, reproduction, workspace, before, origin, steps, keep_workspace
        )
    steps.append("installation succeeded")

    reproduction.pip_check = pip_check.run_pip_check(workspace, env)
    steps.append("pip check completed")

    if report.project.path:
        reproduction.installed_distribution = runtime.read_installed_version(
            workspace, facts.distribution_name or "", env
        )
        if reproduction.installed_distribution.version:
            steps.append(
                f"installed version: {reproduction.installed_distribution.version}"
            )

    if runtime_checks:
        _run_runtime_checks(reproduction, workspace, facts, env, steps)

    return _finish(
        report, reproduction, workspace, before, origin, steps, keep_workspace
    )


def _reproduce_conda(
    report: ScanReport,
    reproduction: ReproductionReport,
    workspace: ws.Workspace,
    before: integrity.Fingerprint,
    origin: Path,
    steps: list[str],
    keep_workspace: bool,
    facts: Facts,
    *,
    env: dict[str, str],
    network: bool,
    environment_file: str | None,
    manager_name: str | None,
    runtime_checks: bool,
    timeout: int,
) -> ScanReport:
    """Create a Conda environment in the workspace and inspect it.

    No virtual environment and no pip install: the environment file is the
    specification, and resolving it is the manager's job. What ReproCheck adds is
    the part a manager does not do: reading back what was actually installed,
    checking it, and reporting whether the Python matches the file.
    """
    attempt = conda.reproduce_conda(
        project=origin,
        facts=facts,
        workspace=workspace,
        env=env,
        network=network,
        environment_file=environment_file,
        manager_name=manager_name,
        timeout=timeout,
    )
    reproduction.conda = attempt
    steps.append(f"strategy: {conda.STRATEGY_CONDA}")

    if attempt.manager:
        steps.append(f"manager: {attempt.manager} {attempt.manager_version or ''}")
    if attempt.reason:
        steps.append(f"stopped: {attempt.reason}")
    if not attempt.success:
        return _finish(
            report, reproduction, workspace, before, origin, steps, keep_workspace
        )
    steps.append("environment created")
    steps.append(
        f"python: {attempt.python_version} "
        f"(declared {attempt.declared_python or 'not declared'})"
    )
    steps.append(f"packages: {attempt.package_count}")
    if attempt.pip_check.ran:
        state = "clean" if attempt.pip_check.clean else "with conflicts"
        steps.append(f"pip check: {state}")

    if runtime_checks and attempt.python_path:
        _run_conda_runtime_checks(reproduction, workspace, facts, env, steps, attempt)

    return _finish(
        report, reproduction, workspace, before, origin, steps, keep_workspace
    )


def _run_conda_runtime_checks(
    reproduction: ReproductionReport,
    workspace: ws.Workspace,
    facts: Facts,
    env: dict[str, str],
    steps: list[str],
    attempt,
) -> None:
    """Import and collect using the environment's own interpreter.

    Same checks as the pip path and the same warning: this executes code from
    the project and from every package in the environment.

    A Conda environment does not contain the project. The environment file
    describes the environment, and installing the project into it would be a pip
    action inside a Conda reproduction, so the import smoke test has nothing of
    the project's own to import and says so. Left unsaid, "distribution not
    installed" reads as a project that failed to install.
    """
    candidates, reason = runtime.discover_import_targets(
        workspace, facts.distribution_name or "", env, python=attempt.python_path
    )
    if not candidates and reason:
        reason = _conda_import_reason(reason)
    reproduction.import_discovery = reason
    reproduction.imports = runtime.run_import_checks(
        workspace, candidates, env, python=attempt.python_path
    )
    steps.append(f"import smoke test: {len(candidates)} candidate(s)")

    reproduction.test_collection = runtime.collect_tests(
        workspace, env, python=attempt.python_path
    )
    steps.append(
        "pytest collection: "
        + (
            "ran"
            if reproduction.test_collection.ran
            else "pytest not installed, skipped"
        )
    )


def _conda_import_reason(reason: str) -> str:
    """Say why a Conda environment offers nothing to import, in those words."""
    if "not installed" in reason:
        return (
            "a Conda environment does not contain the project, so there is no "
            "module of its own to import; the packages the environment does "
            "contain were not imported either"
        )
    return reason


def _run_runtime_checks(
    reproduction: ReproductionReport,
    workspace: ws.Workspace,
    facts: Facts,
    env: dict[str, str],
    steps: list[str],
) -> None:
    """Import the installed modules and collect tests. Executes project code."""
    candidates, reason = runtime.discover_import_targets(
        workspace, facts.distribution_name or "", env
    )
    reproduction.import_discovery = reason
    reproduction.imports = runtime.run_import_checks(workspace, candidates, env)
    steps.append(f"import smoke test: {len(candidates)} candidate(s)")

    reproduction.test_collection = runtime.collect_tests(workspace, env)
    steps.append(
        "pytest collection: "
        + (
            "ran"
            if reproduction.test_collection.ran
            else "pytest not installed, skipped"
        )
    )


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #


def _create_venv(
    executable: str, workspace: ws.Workspace, env: dict[str, str], timeout: int
) -> VenvInfo:
    """Create the virtual environment outside the project copy."""
    started = time.monotonic()
    result = ws.run_command(
        [executable, "-m", "venv", str(workspace.venv)],
        cwd=workspace.root,
        logs=workspace.logs,
        name="venv",
        env=env,
        timeout=timeout,
    )
    duration = round(time.monotonic() - started, 3)
    python = installation.venv_python(workspace)
    if not result.succeeded or not Path(python).exists():
        return VenvInfo(
            path=str(workspace.venv),
            python=python,
            duration_seconds=duration,
            created=False,
            error=snippet(result.stderr_snippet or result.stdout_snippet, 400),
        )

    pip_version = _pip_version(python, workspace, env)
    return VenvInfo(
        path=str(workspace.venv),
        python=python,
        python_version=_probe_version(python, workspace, env),
        initial_pip_version=pip_version,
        created=True,
        duration_seconds=duration,
    )


def _pip_version(
    python: str, workspace: ws.Workspace, env: dict[str, str]
) -> str | None:
    result = ws.run_command(
        [python, "-m", "pip", "--version"],
        cwd=workspace.root,
        logs=workspace.logs,
        name="pip-version",
        env=env,
        timeout=120,
    )
    if not result.succeeded:
        return None
    first = result.stdout_snippet.splitlines()[:1]
    return first[0].strip() if first else None


def _probe_version(
    python: str, workspace: ws.Workspace, env: dict[str, str]
) -> str | None:
    result = ws.run_command(
        [python, "-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"],
        cwd=workspace.root,
        logs=workspace.logs,
        name="python-version",
        env=env,
        timeout=120,
    )
    return result.stdout_snippet.strip() or None if result.succeeded else None


# --------------------------------------------------------------------------- #
# Finalisation
# --------------------------------------------------------------------------- #


def _finish(
    report: ScanReport,
    reproduction: ReproductionReport,
    workspace: ws.Workspace,
    before: integrity.Fingerprint,
    origin: Path,
    steps: list[str],
    keep_workspace: bool,
) -> ScanReport:
    """Verify the original project, then apply the cleanup policy."""
    reproduction.completed_steps = tuple(steps)
    reproduction.integrity = integrity.compare(before, integrity.capture(origin))
    failed = bool(reproduction.integrity.changed_paths) or not _succeeded(reproduction)

    # Policy: a successful attempt is cleaned up; a failed one keeps its
    # workspace, because that is where the evidence lives.
    if keep_workspace or failed:
        reproduction.workspace_kept = True
    else:
        ws.cleanup(workspace)
        reproduction.workspace = None

    report.reproduction = reproduction.to_dict()
    report.reproduction_findings = check_reproduction(
        reproduction, reprocheck_version=__version__
    )
    report.verdict = compute_verdict(report)
    return report


def _succeeded(report: ReproductionReport) -> bool:
    """Whether the attempt proved what it set out to prove.

    The two strategies are judged by their own criteria. A Conda run has no pip
    installation step, so requiring one would make every successful Conda
    attempt look like a failure, keep its workspace, and turn a clean result
    into a permanent PARTIAL.
    """
    if report.conda is not None:
        return bool(
            report.conda.success
            and report.conda.python_version
            and not (report.conda.pip_check.ran and not report.conda.pip_check.clean)
        )
    if report.installation is None:
        return False
    return bool(report.installation.success and report.pip_check.clean)


def _resolve(project: str | Path) -> Path:
    path = Path(project).expanduser()
    if not path.exists():
        raise ReproductionError(f"path does not exist: {path}")
    if not path.is_dir():
        raise ReproductionError(f"path is not a directory: {path}")
    return path.resolve()


def _conda_subprocess_env() -> dict[str, str]:
    """Environment for the manager and for the environment's own interpreter.

    The import path is cleared. A Conda prefix that can still see the host's
    ``PYTHONPATH`` or the user site directory is not the environment it claims to
    be: a module would import from outside the prefix and the reproduction would
    be reporting something that is not installed there.
    """
    env = dict(os.environ)
    env["PYTHONNOUSERSITE"] = "1"
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["CONDA_ALWAYS_YES"] = "1"
    return env


def _subprocess_env(*, network: bool) -> dict[str, str]:
    """Environment for pip: no prompts, no bytecode writes, explicit index."""
    env = dict(os.environ)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PIP_INDEX_URL", None)
    if not network:
        env["PIP_NO_INDEX"] = "1"
    return env
