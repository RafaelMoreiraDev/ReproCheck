"""Detection of Python package manager signals.

Every signal is reported with its evidence. When more than one tool is
signalled, all of them are kept: V0.1 never picks a single winner.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from reprocheck.models import PackageManagerHint

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
