"""Read-only Git inspection.

Only the commands listed in :data:`READ_ONLY_ARGS` are ever executed. No
command in this module can modify the working tree, the index, refs or the
repository configuration.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from reprocheck.models import GitInfo

# ``--no-optional-locks`` prevents Git from refreshing (and therefore writing
# to) the index during status checks.
_BASE_ARGS = ("--no-optional-locks",)

COMMAND_IS_REPO = "rev-parse", "--is-inside-work-tree"
COMMAND_TOPLEVEL = "rev-parse", "--show-toplevel"
COMMAND_BRANCH = "rev-parse", "--abbrev-ref", "HEAD"
COMMAND_HEAD = "rev-parse", "HEAD"
COMMAND_STATUS = "status", "--porcelain"

_TIMEOUT_SECONDS = 30


def _run_git(path: Path, args: tuple[str, ...]) -> tuple[int, str, str | None]:
    """Run a read-only Git command, returning ``(code, stdout, error)``."""
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    try:
        completed = subprocess.run(  # noqa: S603
            ["git", "-C", str(path), *_BASE_ARGS, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except FileNotFoundError:
        return (127, "", "git executable not found")
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        return (1, "", str(exc))
    error = completed.stderr.strip() or None
    return (completed.returncode, completed.stdout.strip(), error)


def _first_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def scan_git(path: Path) -> GitInfo:
    """Return Git metadata for ``path``.

    A parent repository is not considered a match: the detected toplevel must
    be the scanned directory itself.
    """
    if not path.is_dir():
        return GitInfo(is_repository=False, git_available=True, error="not a directory")

    code, out, error = _run_git(path, COMMAND_IS_REPO)
    if code != 0 or _first_line(out) != "true":
        return GitInfo(is_repository=False, error=error or "not a git repository")

    code, out, toplevel_error = _run_git(path, COMMAND_TOPLEVEL)
    toplevel = _first_line(out) if code == 0 else None
    if toplevel is not None and not _same_dir(toplevel, path):
        return GitInfo(
            is_repository=False,
            toplevel=toplevel,
            error="inside a parent git repository, not a repository root",
        )

    branch, head, is_clean, dirty = _read_state(path)
    return GitInfo(
        is_repository=True,
        toplevel=toplevel,
        branch=branch,
        head=head,
        is_clean=is_clean,
        dirty_entries=dirty,
        error=toplevel_error,
    )


def _read_state(path: Path) -> tuple[str | None, str | None, bool | None, int]:
    code, out, _ = _run_git(path, COMMAND_BRANCH)
    branch = _first_line(out) if code == 0 else None
    if branch == "HEAD":  # detached HEAD
        branch = None

    code, out, _ = _run_git(path, COMMAND_HEAD)
    head = _first_line(out) if code == 0 else None

    code, out, _ = _run_git(path, COMMAND_STATUS)
    if code != 0:
        return (branch, head, None, 0)
    entries = [line for line in out.splitlines() if line.strip()]
    return (branch, head, not entries, len(entries))


def _same_dir(left: str, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(str(right))
    )
