"""Isolated workspace for a reproduction attempt.

The analysed project is **copied** into the workspace and never used in place.
Nothing is written to the original: the venv, the logs and the report all live
under the temporary workspace.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from reprocheck.reproduction.models import CommandResult, snippet

WORKSPACE_ROOT_NAME = "reprocheck"

#: Never copied into the workspace: version control, environments and caches.
EXCLUDED_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "node_modules",
        "build",
        "dist",
        ".ipynb_checkpoints",
    }
)

EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".pyd", ".so", ".egg-info")

#: Files larger than this are assumed to be generated data, not source.
MAX_COPIED_BYTES = 64 * 1024 * 1024

DEFAULT_TIMEOUT = 900


@dataclass(frozen=True, slots=True)
class Workspace:
    """A temporary directory holding the isolated copy of a project."""

    root: Path
    source: Path
    venv: Path
    logs: Path
    artifacts: Path
    report: Path
    kept: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "source": str(self.source),
            "venv": str(self.venv),
            "logs": str(self.logs),
            "artifacts": str(self.artifacts),
            "report": str(self.report),
        }


def create_workspace(base: Path | None = None) -> Workspace:
    """Create an empty workspace with the fixed directory layout."""
    root_base = Path(base) if base else Path(tempfile_dir())
    root_base.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    root = root_base / WORKSPACE_ROOT_NAME / run_id
    source = root / "source"
    venv = root / "venv"
    logs = root / "logs"
    artifacts = root / "artifacts"
    for directory in (root, source, logs, artifacts):
        directory.mkdir(parents=True, exist_ok=True)
    return Workspace(
        root=root,
        source=source,
        venv=venv,
        logs=logs,
        artifacts=artifacts,
        report=root / "report.json",
    )


def tempfile_dir() -> str:
    return os.environ.get("TEMP") or os.environ.get("TMP") or str(Path.home())


def copy_project(origin: Path, workspace: Workspace) -> dict[str, int]:
    """Copy ``origin`` into the workspace, excluding environments and caches.

    Returns a small summary of what was skipped, for the run report.
    """
    skipped: dict[str, int] = {}
    for current, dirnames, filenames in os.walk(origin, onerror=None):
        for name in list(dirnames):
            if _skip_dir(name):
                dirnames.remove(name)
                skipped[name] = skipped.get(name, 0) + 1
        relative_dir = Path(current).relative_to(origin)
        for name in sorted(filenames):
            source_file = Path(current) / name
            if _skip_file(source_file):
                skipped[name] = skipped.get(name, 0) + 1
                continue
            target = workspace.source / relative_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source_file, target)
            except OSError as exc:  # pragma: no cover - defensive
                skipped[f"{name} ({exc.__class__.__name__})"] = 1
    return {"skipped": len(skipped), **skipped}


def _skip_dir(name: str) -> bool:
    return name in EXCLUDED_NAMES or name.endswith(".egg-info")


def _skip_file(path: Path) -> bool:
    if path.suffix in EXCLUDED_SUFFIXES or path.suffix == ".lock":
        return True
    if path.name.endswith(".egg-info"):
        return True
    try:
        return path.stat().st_size > MAX_COPIED_BYTES
    except OSError:  # pragma: no cover - defensive
        return True


def cleanup(workspace: Workspace) -> None:
    """Delete the workspace tree. Never raises."""
    shutil.rmtree(workspace.root, ignore_errors=True)


def run_command(
    command: list[str],
    *,
    cwd: Path,
    logs: Path,
    name: str,
    env: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> CommandResult:
    """Run a command, streaming its output into the workspace log directory.

    The result keeps only truncated snippets; the full output stays in the
    workspace, so the JSON report can never become a log dump.
    """
    logs.mkdir(parents=True, exist_ok=True)
    stdout_path = logs / f"{name}.stdout.log"
    stderr_path = logs / f"{name}.stderr.log"
    started = time.monotonic()
    try:
        with (
            stdout_path.open("w", encoding="utf-8", errors="replace") as out,
            stderr_path.open("w", encoding="utf-8", errors="replace") as err,
        ):
            completed = subprocess.run(  # noqa: S603
                command,
                cwd=str(cwd),
                stdout=out,
                stderr=err,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env=env,
            )
        exit_code = completed.returncode
    except subprocess.TimeoutExpired:
        exit_code = 124
        _append(stderr_path, f"reprocheck: command timed out after {timeout}s")
    except OSError as exc:
        exit_code = 127
        _append(stderr_path, f"reprocheck: could not run command: {exc}")
    duration = round(time.monotonic() - started, 3)

    return CommandResult(
        command=tuple(command),
        exit_code=exit_code,
        duration_seconds=duration,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        stdout_snippet=snippet(_read(stdout_path)),
        stderr_snippet=snippet(_read(stderr_path)),
    )


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(text + "\n")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - defensive
        return ""


def read_log(path: Path) -> str:
    """Read a workspace log file, returning an empty string when missing."""
    return _read(path)
