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
from reprocheck.reproduction import fingerprint as integrity
from reprocheck.reproduction import installer as installation
from reprocheck.reproduction import pip_check, python_selector, runtime
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
) -> ScanReport:
    """Attempt a controlled reproduction of ``project``.

    ``runtime_checks`` is opt-in: it imports the installed package and collects
    tests, which executes project code. The original project is never modified.
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
