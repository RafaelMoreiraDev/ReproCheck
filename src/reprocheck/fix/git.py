"""Read-only Git observation around a fix.

Only ``rev-parse`` and ``status --porcelain`` are executed, with
``--no-optional-locks`` so Git does not even refresh its index. Nothing here
commits, branches, checks out, resets or cleans: a fix is a file write, and the
repository is only *observed*.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from reprocheck.fix.models import GitObservation

_TIMEOUT_SECONDS = 30


def observe(root: Path, target: str | None = None) -> GitObservation:
    """Return the HEAD, the changed paths and whether the target is modified."""
    head = _run(root, "rev-parse", "HEAD")
    if head is None:
        inside = _run(root, "rev-parse", "--is-inside-work-tree")
        if inside is None or inside.strip() != "true":
            return GitObservation(is_repository=False, error="not a Git repository")
    status = _run(root, "status", "--porcelain")
    paths = _changed_paths(status)
    target_modified = False
    if target:
        specific = _run(root, "status", "--porcelain", "--", target)
        target_modified = bool(specific and specific.strip())
    return GitObservation(
        is_repository=True,
        head=head.strip() if head else None,
        status=status,
        changed_paths=paths,
        target_already_modified=target_modified,
    )


def _changed_paths(status: str | None) -> tuple[str, ...]:
    if not status:
        return ()
    paths = []
    for line in status.splitlines():
        if len(line) > 3:
            paths.append(line[3:].strip().strip('"'))
    return tuple(sorted(path for path in paths if path))


def _run(root: Path, *args: str) -> str | None:
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    try:
        completed = subprocess.run(  # noqa: S603
            ["git", "-C", str(root), "--no-optional-locks", *args],  # noqa: S607
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - no git
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout
