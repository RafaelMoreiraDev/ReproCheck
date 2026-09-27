"""Deterministic installation strategies.

Only explicit, well-understood strategies are supported. Nothing is invented:
when no strategy applies, the reproduction reports RC403 instead of guessing.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from reprocheck.facts import Facts
from reprocheck.reproduction.models import InstallationAttempt
from reprocheck.reproduction.workspace import Workspace, run_command

STRATEGY_PROJECT = "project"
STRATEGY_REQUIREMENTS = "requirements"
STRATEGY_NONE = "none"

#: Always applied: no prompts, no version-check network call.
BASE_PIP_FLAGS = ("--disable-pip-version-check", "--no-input")

INSTALL_TIMEOUT = 1800

#: Error text that proves the install needed to reach a remote index.
NETWORK_ERROR_MARKERS = (
    "no matching distribution found",
    "could not find a version that satisfies",
    "connection to",
    "temporary failure in name resolution",
    "failed to establish a new connection",
    "proxyerror",
    "network is unreachable",
    "read timed out",
    "could not fetch",
    "unable to download",
    "requires a network",
)


@dataclass(frozen=True, slots=True)
class Strategy:
    """A resolved installation strategy."""

    name: str
    command: tuple[str, ...]
    reason: str


def detect_strategy(facts: Facts, workspace: Workspace) -> Strategy:
    """Choose how to install, from facts and from what exists in the copy.

    ``project`` installs the copied project with a non-editable
    ``pip install``: it exercises the declared build backend and the declared
    dependencies, which is what a real installation does. An editable install
    would hide packaging errors behind a path entry.
    """
    if _is_installable_project(facts):
        command = (
            venv_python(workspace),
            "-m",
            "pip",
            "install",
            *BASE_PIP_FLAGS,
            str(workspace.source),
        )
        return Strategy(
            name=STRATEGY_PROJECT,
            command=command,
            reason="pyproject/setup.py declares an installable project",
        )

    requirements = workspace.source / "requirements.txt"
    if requirements.is_file():
        command = (
            venv_python(workspace),
            "-m",
            "pip",
            "install",
            *BASE_PIP_FLAGS,
            "-r",
            str(requirements),
        )
        return Strategy(
            name=STRATEGY_REQUIREMENTS,
            command=command,
            reason="requirements.txt exists and the project is not installable",
        )

    return Strategy(
        name=STRATEGY_NONE,
        command=(),
        reason=(
            "no pyproject.toml with [project] or setup.py, and no requirements.txt"
        ),
    )


def _is_installable_project(facts: Facts) -> bool:
    """True when the copy can be installed with ``pip install <source>``.

    A ``[project]`` table, a distribution name, any runtime declaration
    (including Poetry tables) or a setup.py/setup.cfg all qualify. A
    ``pyproject.toml`` that only configures tools does not.
    """
    if facts.has_file("setup.py") or facts.has_file("setup.cfg"):
        return True
    if not facts.has_file("pyproject.toml"):
        return False
    if facts.distribution_name:
        return True
    return any(item.kind == "runtime" for item in facts.dependency_declarations)


def venv_python(workspace: Workspace) -> str:
    """Path of the interpreter inside the workspace venv."""
    if sys.platform == "win32":
        return str(workspace.venv / "Scripts" / "python.exe")
    return str(workspace.venv / "bin" / "python")


def install(
    strategy: Strategy,
    workspace: Workspace,
    *,
    original: Path,
    network: bool,
    env: dict[str, str],
) -> InstallationAttempt:
    """Run the chosen strategy inside the workspace copy."""
    command = list(strategy.command)
    if not network:
        command = [*command[:4], "--no-index", *command[4:]]

    result = run_command(
        command,
        cwd=workspace.source,
        logs=workspace.logs,
        name="install",
        env=env,
        timeout=INSTALL_TIMEOUT,
    )
    return InstallationAttempt(
        strategy=strategy.name,
        command=tuple(command),
        cwd=str(workspace.source),
        source=str(original),
        venv=str(workspace.venv),
        network_enabled=network,
        exit_code=result.exit_code,
        duration_seconds=result.duration_seconds,
        success=result.succeeded,
        stdout_path=result.stdout_path,
        stderr_path=result.stderr_path,
        stdout_snippet=result.stdout_snippet,
        stderr_snippet=result.stderr_snippet,
    )


def needs_network(attempt: InstallationAttempt) -> bool:
    """True when the install output proves an external index was required.

    The conclusion is drawn from the error text only: no request is ever made
    to confirm it.
    """
    text = f"{attempt.stdout_snippet}\n{attempt.stderr_snippet}".lower()
    return any(marker in text for marker in NETWORK_ERROR_MARKERS)
