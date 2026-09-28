"""Where reports and baselines are written.

V0.7 wrote every report to the current working directory, which silently
dirties a repository when ReproCheck is run from inside the project it is
analysing. The default is now a **per-user state directory**, so the only thing
ReproCheck writes inside a project is nothing at all.

The state directory is resolved in this order:

1. ``REPROCHECK_STATE_DIR``, when set (used by the test suite and by CI);
2. ``%LOCALAPPDATA%\\reprocheck`` on Windows;
3. ``$XDG_STATE_HOME/reprocheck`` or ``~/.local/state/reprocheck`` elsewhere.

Inside it, reports are grouped by project so that two projects with the same
name never overwrite each other::

    <state>/reprocheck/<name>-<8 hex of the project path>/reprocheck-report.json
    <state>/reprocheck/<name>-<8 hex of the project path>/reprocheck-report.md
    <state>/reprocheck/<name>-<8 hex of the project path>/reprocheck-diff.json
    <state>/reprocheck/<name>-<8 hex of the project path>/reprocheck-diff.md
    <state>/reprocheck/<name>-<8 hex of the project path>/baseline.json

Explicit destinations (``--json``, ``--markdown``, ``--output-dir``,
``--output``) always win and are used verbatim, so an existing script that
passes its own path keeps working unchanged.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

STATE_DIR_ENV = "REPROCHECK_STATE_DIR"

REPORT_NAME = "reprocheck-report.json"
REPORT_MARKDOWN_NAME = "reprocheck-report.md"
DIFF_NAME = "reprocheck-diff.json"
DIFF_MARKDOWN_NAME = "reprocheck-diff.md"
BASELINE_NAME = "baseline.json"
SUGGESTIONS_NAME = "reprocheck-suggestions.json"
SUGGESTIONS_MARKDOWN_NAME = "reprocheck-suggestions.md"

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class OutputError(Exception):
    """Raised when a destination cannot be used."""


def state_dir() -> Path:
    """Return the per-user state directory of ReproCheck."""
    override = os.environ.get(STATE_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
    else:
        xdg = os.environ.get("XDG_STATE_HOME")
        base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "reprocheck"


def project_identity(project: str | Path) -> str:
    """Return the stable identity of a project: its normalised absolute path.

    This is the only identity available for a local project. The Git HEAD is
    deliberately **not** part of it: a commit changes on every commit, so it
    would make every comparison of the same project look like a different one.
    The project name alone is not enough either: two repositories can share it.

    ``project_output_dir`` derives its directory from this same string, so the
    state directory and the identity check can never disagree.
    """
    resolved = Path(project).expanduser().resolve()
    text = str(resolved)
    if sys.platform == "win32":
        text = text.replace("/", "\\").rstrip("\\").lower()
    return text


def project_output_dir(project: str | Path | None, *, root: Path | None = None) -> Path:
    """Return the default output directory for one analysed project."""
    base = root if root is not None else state_dir()
    if not project:
        return base
    identity = project_identity(project)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
    name = _UNSAFE.sub("-", Path(identity).name) or "project"
    return base / f"{name}-{digest}"


def resolve_destination(
    explicit: str | Path | None,
    default_name: str,
    output_dir: str | Path | None,
    project: str | Path | None,
) -> Path:
    """Resolve one report destination.

    An explicit path is used verbatim. Otherwise the file goes into
    ``output_dir`` when given, or into the per-project state directory.
    """
    if explicit:
        return Path(explicit).expanduser()
    directory = (
        Path(output_dir).expanduser() if output_dir else project_output_dir(project)
    )
    return directory / default_name
