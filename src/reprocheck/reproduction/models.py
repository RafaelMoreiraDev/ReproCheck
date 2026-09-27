"""Typed models for the reproduction attempt.

Every field is a fact observed during the run. Nothing here states a verdict:
verdicts belong to findings, produced by the checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Log snippets are truncated so the report never becomes a log dump.
MAX_SNIPPET_CHARS = 2000


def snippet(text: str, limit: int = MAX_SNIPPET_CHARS) -> str:
    """Return the tail of ``text``, truncated to at most ``limit`` characters."""
    cleaned = (text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return "..." + cleaned[-(limit - 3) :]


@dataclass(frozen=True, slots=True)
class InterpreterCandidate:
    """A Python interpreter found on this machine."""

    executable: str
    version: str
    source: str

    def to_dict(self) -> dict[str, object]:
        return {
            "executable": self.executable,
            "version": self.version,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class PythonSelection:
    """How the interpreter to reproduce with was chosen."""

    selected: str | None = None
    executable: str | None = None
    reason: str = ""
    requirement: str | None = None
    candidates: tuple[InterpreterCandidate, ...] = ()
    rejected: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "selected": self.selected,
            "executable": self.executable,
            "reason": self.reason,
            "requirement": self.requirement,
            "candidates": [item.to_dict() for item in self.candidates],
            "rejected": list(self.rejected),
        }


@dataclass(frozen=True, slots=True)
class VenvInfo:
    """The virtual environment created for the attempt."""

    path: str | None = None
    python: str | None = None
    python_version: str | None = None
    initial_pip_version: str | None = None
    created: bool = False
    duration_seconds: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "python": self.python,
            "python_version": self.python_version,
            "initial_pip_version": self.initial_pip_version,
            "created": self.created,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The outcome of one subprocess, with logs kept in the workspace."""

    command: tuple[str, ...]
    exit_code: int
    duration_seconds: float
    stdout_path: str | None = None
    stderr_path: str | None = None
    stdout_snippet: str = ""
    stderr_snippet: str = ""

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0

    def to_dict(self) -> dict[str, object]:
        return {
            "command": list(self.command),
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
            "stdout_snippet": self.stdout_snippet,
            "stderr_snippet": self.stderr_snippet,
        }


@dataclass(frozen=True, slots=True)
class InstallationAttempt:
    """One deterministic installation attempt inside the workspace."""

    strategy: str
    command: tuple[str, ...]
    cwd: str
    source: str
    venv: str | None
    network_enabled: bool
    exit_code: int | None = None
    duration_seconds: float | None = None
    success: bool = False
    stdout_path: str | None = None
    stderr_path: str | None = None
    stdout_snippet: str = ""
    stderr_snippet: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "command": list(self.command),
            "cwd": self.cwd,
            "source": self.source,
            "venv": self.venv,
            "network_enabled": self.network_enabled,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "success": self.success,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
            "stdout_snippet": self.stdout_snippet,
            "stderr_snippet": self.stderr_snippet,
        }


@dataclass(frozen=True, slots=True)
class PipCheckResult:
    """Structured result of ``python -m pip check``."""

    ran: bool = False
    exit_code: int | None = None
    clean: bool | None = None
    conflict_count: int = 0
    conflicts: tuple[str, ...] = ()
    duration_seconds: float | None = None
    stdout_snippet: str = ""
    stderr_snippet: str = ""
    stdout_path: str | None = None
    stderr_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "ran": self.ran,
            "exit_code": self.exit_code,
            "clean": self.clean,
            "conflict_count": self.conflict_count,
            "conflicts": list(self.conflicts),
            "duration_seconds": self.duration_seconds,
            "stdout_snippet": self.stdout_snippet,
            "stderr_snippet": self.stderr_snippet,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
        }


@dataclass(frozen=True, slots=True)
class InstalledDistribution:
    """The distribution installed in the reproduced environment."""

    name: str | None = None
    version: str | None = None
    looks_like_fallback: bool = False
    note: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "looks_like_fallback": self.looks_like_fallback,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class ImportCheck:
    """One ``import <module>`` attempt inside the reproduced venv."""

    module: str
    imported: bool = False
    exit_code: int | None = None
    duration_seconds: float | None = None
    timed_out: bool = False
    stderr_snippet: str = ""
    stdout_path: str | None = None
    stderr_path: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "module": self.module,
            "imported": self.imported,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "timed_out": self.timed_out,
            "stderr_snippet": self.stderr_snippet,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class TestCollection:
    """``pytest --collect-only`` inside the reproduced venv."""

    available: bool = False
    ran: bool = False
    success: bool | None = None
    exit_code: int | None = None
    collected: int | None = None
    duration_seconds: float | None = None
    stdout_snippet: str = ""
    stderr_snippet: str = ""
    stdout_path: str | None = None
    stderr_path: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "ran": self.ran,
            "success": self.success,
            "exit_code": self.exit_code,
            "collected": self.collected,
            "duration_seconds": self.duration_seconds,
            "stdout_snippet": self.stdout_snippet,
            "stderr_snippet": self.stderr_snippet,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class FingerprintResult:
    """Comparison of the analysed project before and after the attempt."""

    captured_before: bool = False
    unchanged: bool = True
    git_head_before: str | None = None
    git_head_after: str | None = None
    git_status_before: str | None = None
    git_status_after: str | None = None
    tracked_files: int = 0
    changed_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "captured_before": self.captured_before,
            "unchanged": self.unchanged,
            "git_head_before": self.git_head_before,
            "git_head_after": self.git_head_after,
            "git_status_before": self.git_status_before,
            "git_status_after": self.git_status_after,
            "tracked_files": self.tracked_files,
            "changed_paths": list(self.changed_paths),
        }


@dataclass(slots=True)
class ReproductionReport:
    """Everything one ``reprocheck reproduce`` run observed."""

    attempted: bool = True
    workspace: str | None = None
    workspace_kept: bool = False
    source: str | None = None
    original_project: str | None = None
    network_enabled: bool = False
    runtime_checks_enabled: bool = False
    python: PythonSelection = field(default_factory=PythonSelection)
    venv: VenvInfo = field(default_factory=VenvInfo)
    installation: InstallationAttempt | None = None
    pip_check: PipCheckResult = field(default_factory=PipCheckResult)
    installed_distribution: InstalledDistribution = field(
        default_factory=InstalledDistribution
    )
    imports: tuple[ImportCheck, ...] = ()
    import_discovery: str | None = None
    test_collection: TestCollection = field(default_factory=TestCollection)
    integrity: FingerprintResult = field(default_factory=FingerprintResult)
    completed_steps: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "attempted": self.attempted,
            "workspace": self.workspace,
            "workspace_kept": self.workspace_kept,
            "source": self.source,
            "original_project": self.original_project,
            "network_enabled": self.network_enabled,
            "runtime_checks_enabled": self.runtime_checks_enabled,
            "python": self.python.to_dict(),
            "venv": self.venv.to_dict(),
            "installation": (
                self.installation.to_dict() if self.installation else None
            ),
            "pip_check": self.pip_check.to_dict(),
            "installed_distribution": self.installed_distribution.to_dict(),
            "runtime_checks": {
                "enabled": self.runtime_checks_enabled,
                "import_discovery": self.import_discovery,
                "imports": [item.to_dict() for item in self.imports],
                "pytest_collection": self.test_collection.to_dict(),
            },
            "original_project_unchanged": self.integrity.unchanged,
            "integrity": self.integrity.to_dict(),
            "completed_steps": list(self.completed_steps),
        }
