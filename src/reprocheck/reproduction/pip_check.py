"""The ``pip check`` step.

``pip check`` answers one question: does the installed environment satisfy the
metadata of everything installed in it. Its output is parsed into structured
conflicts, not stored as raw text.
"""

from __future__ import annotations

import re
from pathlib import Path

from reprocheck.reproduction.installer import venv_python
from reprocheck.reproduction.models import PipCheckResult
from reprocheck.reproduction.workspace import Workspace, run_command

CHECK_TIMEOUT = 300

#: ``beta 1.0 has requirement sharedlib==2.0, but you have sharedlib 1.0.``
_MISMATCH_RE = re.compile(
    r"^(?P<package>\S+)\s+\S+\s+has requirement\s+(?P<required>.+?),\s*"
    r"but you have\s+(?P<found>\S+)\s+(?P<version>\S+?)\.?\s*$"
)
#: ``broken 1.0 requires missingdep 2.0, which is not installed.``
_MISSING_RE = re.compile(
    r"^(?P<package>\S+)\s+\S+\s+requires\s+(?P<required>.+?),\s*"
    r"which is not installed\.?\s*$"
)

#: ``wcwidth 0.9.1 is not supported on this platform``, seen in a real tqdm
#: environment. pip exits non-zero for a distribution it refuses to install
#: rather than for a version mismatch, and the three patterns above recognise
#: none of it.
_UNSUPPORTED_RE = re.compile(
    r"^(?P<package>\S+)\s+(?P<version>\S+)\s+is not supported on this platform\.?\s*$"
)

_BROKEN_MARKERS = ("broken environment", "the environment is broken")


def run_pip_check(workspace: Workspace, env: dict[str, str]) -> PipCheckResult:
    """Run ``python -m pip check`` inside the workspace venv."""
    result = run_command(
        [venv_python(workspace), "-m", "pip", "check"],
        cwd=workspace.source,
        logs=workspace.logs,
        name="pip-check",
        env=env,
        timeout=CHECK_TIMEOUT,
    )
    conflicts = parse_conflicts(_read_all(result.stdout_path, result.stderr_path))
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


def parse_conflicts(output: str) -> tuple[str, ...]:
    """Extract the individual conflicts reported by ``pip check``."""
    conflicts: list[str] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        mismatch = _MISMATCH_RE.match(line)
        if mismatch:
            conflicts.append(
                f"{mismatch.group('package')} requires "
                f"{mismatch.group('required')}, but {mismatch.group('found')} "
                f"{mismatch.group('version')} is installed"
            )
            continue
        missing = _MISSING_RE.match(line)
        if missing:
            conflicts.append(
                f"{missing.group('package')} requires "
                f"{missing.group('required')}, which is not installed"
            )
            continue
        unsupported = _UNSUPPORTED_RE.match(line)
        if unsupported:
            conflicts.append(
                f"{unsupported.group('package')} {unsupported.group('version')} "
                f"is not supported on this platform, so pip refuses to install it"
            )
            continue
        if any(marker in line.lower() for marker in _BROKEN_MARKERS):
            conflicts.append(line)
    return tuple(conflicts)


def _read_all(*paths: str | Path | None) -> str:
    text: list[str] = []
    for path in paths:
        if not path:
            continue
        try:
            text.append(Path(path).read_text(encoding="utf-8", errors="replace"))
        except OSError:  # pragma: no cover - defensive
            continue
    return "".join(text)
