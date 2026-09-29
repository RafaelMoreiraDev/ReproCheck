"""The Conda reproduction pipeline.

The steps, in the order they run and the order they can stop in::

    resolve the environment file       (RC601 when it is ambiguous)
    resolve network permission        (RC604 when it was not given)
    discover a manager                (RC600 when none exists)
    create the environment            (RC602 when it fails)
    read the environment's own Python (RC603 when it does not match)
    list what was installed
    pip check, when the file has a pip subsection
    runtime checks, only when asked

Two rules shape the whole module.

**Nothing is installed.** If no manager is present the attempt stops and says
which three were looked for. Downloading a package manager would be a larger
action than the reproduction, and it would be invisible in the report.

**No step is silent.** Each one records what it decided, and a step that did
not run records why. The report never leaves the reader guessing whether
something was skipped or simply forgotten.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path

from reprocheck.facts import Facts
from reprocheck.reproduction.conda_manager import (
    CondaManager,
    describe_failure,
    environment_python_version,
    read_log,
)
from reprocheck.reproduction.models import CondaReproduction, PipCheckResult
from reprocheck.reproduction.pip_check import parse_conflicts
from reprocheck.reproduction.workspace import Workspace, run_command
from reprocheck.scanners.conda import parse_environment

STRATEGY_CONDA = "conda"
STRATEGY_PIP = "pip"

#: Reasons a Conda attempt stopped, as recorded facts rather than findings.
STOP_NO_MANAGER = "no-manager"
STOP_NO_ENVIRONMENT_FILE = "no-environment-file"
STOP_AMBIGUOUS_ENVIRONMENT = "ambiguous-environment"
STOP_NO_NETWORK = "no-network"
STOP_UNKNOWN_MANAGER = "unknown-manager"


@dataclass(frozen=True, slots=True)
class EnvironmentChoice:
    """Which environment file to reproduce, and whether that is certain.

    ``file`` is ``None`` when nothing usable was found, and ``reason`` says
    why. A guess between several files is never made: a reproduction that
    silently picked one would be a claim the user never asked for.
    """

    file: Path | None = None
    candidates: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def is_ambiguous(self) -> bool:
        return len(self.candidates) > 1 and self.file is None


def choose_environment(
    facts: Facts, *, explicit: str | None = None
) -> EnvironmentChoice:
    """Decide which environment file to reproduce.

    With ``--conda-env`` the choice is the user's and is used verbatim, so a
    typo is reported by the manager rather than guessed around. Without it, a
    single file is used and several are refused.
    """
    available = [
        environment.file
        for environment in sorted(facts.conda_environments, key=lambda item: item.file)
    ]
    if explicit:
        target = Path(explicit)
        return EnvironmentChoice(file=target, candidates=tuple(available))
    if not available:
        return EnvironmentChoice(candidates=(), reason=STOP_NO_ENVIRONMENT_FILE)
    if len(available) > 1:
        return EnvironmentChoice(
            candidates=tuple(available), reason=STOP_AMBIGUOUS_ENVIRONMENT
        )
    return EnvironmentChoice(file=Path(available[0]), candidates=tuple(available))


def declared_python_spec(root: Path, relative: Path) -> str | None:
    """The Python spec the environment file declares, or ``None``.

    The **specifier**, not the whole line: ``python=3.11`` declares ``=3.11``,
    and comparing the whole line would make every translation fail and silently
    suppress RC603 for every project.
    """
    target = root / relative
    if not target.is_file():
        return None
    environment = parse_environment(target, root)
    declaration = environment.python_dependency
    if declaration is None:
        return None
    return declaration.raw_spec or None


def reproduce_conda(
    *,
    project: Path,
    facts: Facts,
    workspace: Workspace,
    env: dict[str, str],
    network: bool,
    environment_file: str | None = None,
    manager_name: str | None = None,
    timeout: int,
) -> CondaReproduction:
    """Attempt a Conda reproduction, stopping at the first blocked step."""
    choice = choose_environment(facts, explicit=environment_file)
    base = CondaReproduction(
        strategy=STRATEGY_CONDA,
        attempted=True,
        network_enabled=network,
        environment_file=str(choice.file) if choice.file else None,
        prefix=str(workspace.conda_prefix),
        declared_python=(
            declared_python_spec(project, choice.file) if choice.file else None
        ),
    )

    if choice.reason is not None and choice.file is None:
        return replace(
            base,
            success=False,
            reason=choice.reason,
            discovery=tuple(choice.candidates),
            error=_ambiguity_message(choice),
        )

    manager = CondaManager.discover(explicit=manager_name)
    if manager is None:
        return replace(
            base,
            success=False,
            reason=STOP_UNKNOWN_MANAGER,
            error=(
                f"{manager_name!r} is not a supported manager; use one of: "
                + ", ".join(
                    spec.name
                    for spec in __import__(
                        "reprocheck.reproduction.conda_manager", fromlist=["MANAGERS"]
                    ).MANAGERS
                )
            ),
        )
    if not manager:
        return replace(
            base,
            success=False,
            reason=STOP_NO_MANAGER,
            discovery=tuple(manager.discovery),
            error=manager.reason,
        )

    # The network gate sits after discovery on purpose: a user who forgot
    # --network should learn both facts at once rather than fixing them one
    # run at a time.
    if not network:
        return replace(
            base,
            success=False,
            reason=STOP_NO_NETWORK,
            manager=manager.name,
            manager_executable=manager.executable,
            discovery=tuple(manager.discovery),
            error=(
                "Conda reproduction requires network permission. Re-run with "
                "--network to allow the solver to reach the channels."
            ),
        )

    manager.read_version(workspace, env)
    steps: dict[str, object] = {
        "manager": manager.name,
        "manager_executable": manager.executable,
        "manager_version": manager.version,
        "discovery": tuple(manager.discovery),
    }
    base = replace(base, **steps)

    assert choice.file is not None  # noqa: S101 - established above
    # The file is resolved against the project copy, not the working directory
    # the manager runs in. The command runs at the workspace root, so a relative
    # name would be looked for there and not found, and the honest failure would
    # be "environment.yml does not exist" for a file that does exist.
    # The file is resolved against the project copy, not the working directory
    # the manager runs in. The command runs at the workspace root, so a relative
    # name would be looked for there and not found, and the honest failure would
    # be "environment.yml does not exist" for a file that does exist.
    environment_file = (
        choice.file if choice.file.is_absolute() else (project / choice.file).resolve()
    )
    started = time.monotonic()
    result = manager.create(
        prefix=workspace.conda_prefix,
        environment_file=environment_file,
        workspace=workspace,
        env=env,
        timeout=timeout,
    )
    base = replace(
        base,
        exit_code=result.exit_code,
        duration_seconds=round(time.monotonic() - started, 3),
        command=tuple(result.command),
        cwd=str(workspace.root),
        stdout_path=result.stdout_path,
        stderr_path=result.stderr_path,
        stdout_snippet=result.stdout_snippet,
        stderr_snippet=result.stderr_snippet,
    )

    python = manager.python_path(workspace.conda_prefix)
    if result.exit_code != 0 or not Path(python).exists():
        return replace(
            base,
            success=False,
            error=describe_failure(result),
        )

    listed = manager.list_packages(
        prefix=workspace.conda_prefix, workspace=workspace, env=env
    )
    base = replace(
        base,
        success=True,
        python_path=python,
        python_version=environment_python_version(python, workspace, env),
        package_count=len(listed.packages),
        pip_package_count=sum(1 for item in listed.packages if item.name == "pip"),
        packages=tuple(item.to_dict() for item in listed.packages),
        # The count is bounded by the **list** output, not the create output:
        # conda prints a human sentence after creating an environment, which
        # would have reported every successful solve as truncated.
        package_list_partial=_partial(listed.result, len(listed.packages)),
    )

    base = replace(
        base, pip_check=_run_pip_check(python, workspace, env, facts, choice.file)
    )
    return base


def _ambiguity_message(choice: EnvironmentChoice) -> str:
    if choice.reason == STOP_AMBIGUOUS_ENVIRONMENT:
        return (
            "multiple Conda environment files were found: "
            + ", ".join(choice.candidates)
            + ". Choose one with --conda-env <file>."
        )
    if choice.reason == STOP_NO_ENVIRONMENT_FILE:
        return (
            "no Conda environment file was found. Nothing was reproduced, and "
            "no file was guessed at."
        )
    return choice.reason or "no environment file"


def _partial(result, found: int) -> bool:
    """True when a list output was truncated and the count is a lower bound.

    Both JSON shapes are accepted: ``conda list`` wraps in an object ending in
    ``}`` and ``micromamba list`` prints a bare array ending in ``]``. Checking
    only for a brace reported every micromamba run as partial, which would have
    turned a warning into a permanent PARTIAL verdict.
    """
    text = read_log(result.stdout_path) if result.stdout_path else ""
    stripped = text.rstrip()
    if not stripped:
        return False
    if result.exit_code != 0:
        return True
    return stripped[-1] not in "}]"


def _run_pip_check(
    python: str,
    workspace: Workspace,
    env: dict[str, str],
    facts: Facts,
    environment_file: Path | None,
) -> PipCheckResult:
    """``pip check`` inside the Conda environment, only when there is pip.

    An environment without a ``pip`` subsection may still contain pip as a
    transitive dependency, in which case the check is meaningful. One with
    neither has nothing to check, and pretending otherwise would report a
    failure that does not exist.
    """
    if not _has_pip(facts, environment_file):
        return PipCheckResult(ran=False)
    result = run_command(
        [python, "-m", "pip", "check"],
        cwd=workspace.root,
        logs=workspace.logs,
        name="conda-pip-check",
        env=env,
        timeout=300,
    )
    conflicts = parse_conflicts(
        (read_log(result.stdout_path) or "") + (read_log(result.stderr_path) or "")
    )
    return PipCheckResult(
        ran=True,
        exit_code=result.exit_code,
        clean=result.exit_code == 0 and not conflicts,
        conflict_count=len(conflicts),
        conflicts=conflicts,
        duration_seconds=result.duration_seconds,
        stdout_snippet=result.stdout_snippet,
        stderr_snippet=result.stderr_snippet,
        stdout_path=result.stdout_path,
        stderr_path=result.stderr_path,
    )


def _has_pip(facts: Facts, environment_file: Path | None) -> bool:
    if environment_file is None:
        return False
    name = environment_file.as_posix()
    for environment in facts.conda_environments:
        if environment.file != name:
            continue
        return environment.declares_pip or environment.has_pip_subsection
    return False
