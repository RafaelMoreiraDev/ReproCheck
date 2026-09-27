"""Detection of Python package manager signals.

Every signal is reported with its evidence. When more than one tool is
signalled, all of them are kept: V0.1 never picks a single winner.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from reprocheck.facts import (
    ROLE_BUILD_BACKEND,
    ROLE_DEPENDENCY_DECLARATION,
    ROLE_INSTALLER,
    ROLE_LOCKFILE,
    SOURCE_CONFIG,
    SOURCE_DOCS,
    SOURCE_FILE,
    PackageManagerSignal,
)
from reprocheck.models import PackageManagerHint
from reprocheck.scanners import readme

MANAGER_PIP = "pip"
MANAGER_SETUPTOOLS = "setuptools"
MANAGER_POETRY = "poetry"
MANAGER_UV = "uv"
MANAGER_PIPENV = "pipenv"

# File-level signals: presence of the file is the evidence.
_FILE_SIGNALS: dict[str, tuple[str, ...]] = {
    "requirements.txt": (MANAGER_PIP,),
    "requirements-dev.txt": (MANAGER_PIP,),
    "setup.py": (MANAGER_SETUPTOOLS,),
    "setup.cfg": (MANAGER_SETUPTOOLS,),
    "poetry.lock": (MANAGER_POETRY,),
    "uv.lock": (MANAGER_UV,),
    "Pipfile": (MANAGER_PIPENV,),
}


def scan_package_manager_hints(root: Path) -> list[PackageManagerHint]:
    """Return one hint per (manager, evidence) pair found in ``root``."""
    hints: list[PackageManagerHint] = []

    for filename, managers in _FILE_SIGNALS.items():
        if (root / filename).is_file():
            for manager in managers:
                hints.append(PackageManagerHint(manager, filename))

    hints.extend(_pyproject_hints(root / "pyproject.toml"))
    return hints


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _pyproject_hints(path: Path) -> list[PackageManagerHint]:
    if not path.is_file():
        return []
    text = _read_text(path)
    if text is None:
        return []
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return []

    hints: list[PackageManagerHint] = []
    build_system = data.get("build-system")
    if isinstance(build_system, dict):
        backend = build_system.get("build-backend")
        if isinstance(backend, str) and backend.strip():
            hints.append(
                PackageManagerHint(
                    _manager_for_backend(backend.strip()),
                    f"pyproject.toml [build-system] build-backend = {backend.strip()}",
                )
            )
        requires = build_system.get("requires")
        if isinstance(requires, list):
            for item in requires:
                manager = _known_manager(item) if isinstance(item, str) else None
                if manager is None:
                    continue
                hints.append(
                    PackageManagerHint(
                        manager,
                        f"pyproject.toml [build-system] requires = {item.strip()}",
                    )
                )

    tool = data.get("tool")
    if isinstance(tool, dict):
        for name, manager in (
            ("poetry", MANAGER_POETRY),
            ("uv", MANAGER_UV),
            ("pipenv", MANAGER_PIPENV),
        ):
            if name in tool:
                hints.append(
                    PackageManagerHint(manager, f"pyproject.toml [tool.{name}] section")
                )
    return hints


def _known_manager(value: str) -> str | None:
    """Return the manager for a known build backend, else ``None``."""
    manager = _manager_for_backend(value)
    return None if manager == "unknown" else manager


def _manager_for_backend(value: str) -> str:
    lowered = value.lower()
    if lowered.startswith("poetry"):
        return MANAGER_POETRY
    if lowered.startswith("uv"):
        return MANAGER_UV
    if lowered.startswith("setuptools"):
        return MANAGER_SETUPTOOLS
    if lowered.startswith("flit"):
        return "flit"
    if lowered.startswith("hatch"):
        return "hatch"
    if lowered.startswith("pdm"):
        return "pdm"
    return "unknown"


# --------------------------------------------------------------------------- #
# Signals: the same observations, classified by the role they play.
# --------------------------------------------------------------------------- #

# Lockfile -> the tool that owns it.
LOCKFILES: dict[str, str] = {
    "poetry.lock": MANAGER_POETRY,
    "uv.lock": MANAGER_UV,
    "Pipfile.lock": MANAGER_PIPENV,
    "pdm.lock": "pdm",
}

# Dependency declaration file -> the tool that reads it.
DECLARATIONS: dict[str, tuple[str, ...]] = {
    "requirements.txt": (MANAGER_PIP,),
    "requirements-dev.txt": (MANAGER_PIP,),
    "Pipfile": (MANAGER_PIPENV,),
    "setup.py": (MANAGER_SETUPTOOLS,),
    "setup.cfg": (MANAGER_SETUPTOOLS,),
}


def scan_package_manager_signals(root: Path) -> list[PackageManagerSignal]:
    """Classify every package manager observation by the role it plays."""
    signals: list[PackageManagerSignal] = []

    for filename, manager in LOCKFILES.items():
        if (root / filename).is_file():
            signals.append(
                PackageManagerSignal(
                    manager, ROLE_LOCKFILE, filename, source=SOURCE_FILE
                )
            )

    for filename, managers in DECLARATIONS.items():
        if not (root / filename).is_file():
            continue
        for manager in managers:
            signals.append(
                PackageManagerSignal(
                    manager,
                    ROLE_DEPENDENCY_DECLARATION,
                    filename,
                    source=SOURCE_FILE,
                )
            )

    signals.extend(_pyproject_signals(root / "pyproject.toml"))
    signals.extend(_readme_signals(root))
    return signals


def _pyproject_signals(path: Path) -> list[PackageManagerSignal]:
    if not path.is_file():
        return []
    text = _read_text(path)
    if text is None:
        return []
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return []

    signals: list[PackageManagerSignal] = []
    build_system = data.get("build-system")
    if isinstance(build_system, dict):
        backend = build_system.get("build-backend")
        if isinstance(backend, str) and backend.strip():
            signals.append(
                PackageManagerSignal(
                    _manager_for_backend(backend.strip()),
                    ROLE_BUILD_BACKEND,
                    f"pyproject.toml [build-system] build-backend = {backend.strip()}",
                    source=SOURCE_CONFIG,
                )
            )

    tool = data.get("tool")
    if isinstance(tool, dict):
        for name, manager in (
            ("poetry", MANAGER_POETRY),
            ("uv", MANAGER_UV),
            ("pipenv", MANAGER_PIPENV),
        ):
            if name in tool:
                signals.append(
                    PackageManagerSignal(
                        manager,
                        ROLE_INSTALLER,
                        f"pyproject.toml [tool.{name}] section",
                        source=SOURCE_CONFIG,
                    )
                )
    return signals


def _readme_signals(root: Path) -> list[PackageManagerSignal]:
    """Package managers that the documentation tells the reader to use."""
    commands = readme.scan_readme_commands(root)
    seen: set[tuple[str, str]] = set()
    signals: list[PackageManagerSignal] = []
    for item in commands:
        manager = _documented_manager(item.command)
        if manager is None:
            continue
        evidence = f"{item.file}:{item.line} ({item.command})"
        key = (manager, evidence)
        if key in seen:
            continue
        seen.add(key)
        signals.append(
            PackageManagerSignal(manager, ROLE_INSTALLER, evidence, source=SOURCE_DOCS)
        )
    return signals


# Documentation command -> the tool that would be used.
_DOCUMENTED_TOOLS: dict[str, str] = {
    "poetry": MANAGER_POETRY,
    "uv": MANAGER_UV,
    "pipenv": MANAGER_PIPENV,
    "pdm": "pdm",
}


def _documented_manager(command: str) -> str | None:
    tokens = [token.lower() for token in command.replace("|", " ").split()]
    for token in tokens:
        if token in _DOCUMENTED_TOOLS:
            return _DOCUMENTED_TOOLS[token]
    return None


# --------------------------------------------------------------------------- #
# Declared project metadata
# --------------------------------------------------------------------------- #

_REQUIREMENT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+")


def requirement_name(value: str) -> str:
    """Return the normalised distribution name of a requirement string."""
    match = _REQUIREMENT_NAME_RE.match(value.strip())
    return match.group(0).lower().replace("_", "-") if match else ""


@dataclass(frozen=True, slots=True)
class ProjectMetadata:
    """The distribution name and the dependencies declared by the project."""

    name: str | None = None
    dependencies: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "dependencies": list(self.dependencies)}


def scan_project_metadata(root: Path) -> ProjectMetadata:
    """Read the distribution name and declared dependencies from pyproject."""
    path = root / "pyproject.toml"
    if not path.is_file():
        return ProjectMetadata()
    text = _read_text(path)
    if text is None:
        return ProjectMetadata()
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return ProjectMetadata()

    project = data.get("project")
    project = project if isinstance(project, dict) else {}
    raw_name = project.get("name")
    raw: list[str] = []
    for source in (
        project.get("dependencies") or [],
        _group_values(project.get("optional-dependencies")),
        _group_values(data.get("dependency-groups")),
    ):
        raw.extend(item for item in source if isinstance(item, str))

    declared = sorted(
        {
            name
            for item in raw
            if (name := requirement_name(item.split(";", 1)[0].split("[", 1)[0]))
        }
    )
    return ProjectMetadata(
        name=raw_name.strip() if isinstance(raw_name, str) else None,
        dependencies=tuple(declared),
    )


def _group_values(value: object) -> list[object]:
    if not isinstance(value, dict):
        return []
    collected: list[object] = []
    for group in value.values():
        if isinstance(group, list):
            collected.extend(group)
    return collected
