"""Detection of development tools the project actually uses.

A tool is only reported when there is objective evidence for it: a
configuration section, a declared dependency, a configuration file or a
documented command. Checks use this to avoid demanding ignore rules for tools
the project never mentions.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from reprocheck.facts import SOURCE_CONFIG, SOURCE_DOCS, SOURCE_FILE, ToolSignal
from reprocheck.scanners import readme
from reprocheck.scanners.packaging import requirement_name as _requirement_name

# Tool name -> configuration file that proves it is used.
CONFIG_FILES: dict[str, str] = {
    ".ruff.toml": "ruff",
    "ruff.toml": "ruff",
    ".mypy.ini": "mypy",
    "mypy.ini": "mypy",
    "pytest.ini": "pytest",
    "tox.ini": "tox",
    "noxfile.py": "nox",
    ".pre-commit-config.yaml": "pre-commit",
    ".flake8": "flake8",
    ".pylintrc": "pylint",
    "environment.yml": "conda",
    "environment.yaml": "conda",
}

# pyproject ``[tool.<name>]`` section -> tool name.
_KNOWN_TOOL_SECTIONS = {
    "ruff": "ruff",
    "mypy": "mypy",
    "pytest": "pytest",
    "black": "black",
    "coverage": "coverage",
    "isort": "isort",
    "pylint": "pylint",
    "setuptools": "setuptools",
}

_DOCUMENTED_TOOLS = {
    "pytest": "pytest",
    "ruff": "ruff",
    "mypy": "mypy",
    "black": "black",
    "tox": "tox",
    "nox": "nox",
    "conda": "conda",
}


def scan_tools(root: Path) -> list[ToolSignal]:
    """Return the tools with objective evidence of use, deduplicated."""
    signals: list[ToolSignal] = []
    seen: set[tuple[str, str]] = set()

    def add(name: str, evidence: str, source: str) -> None:
        key = (name, evidence)
        if key in seen:
            return
        seen.add(key)
        signals.append(ToolSignal(name, evidence, source))

    for filename, name in CONFIG_FILES.items():
        if (root / filename).is_file():
            add(name, filename, SOURCE_FILE)

    for item in _pyproject_tools(root / "pyproject.toml"):
        add(*item)
    for item in _requirement_tools(root):
        add(*item)
    for item in _documented_tools(root):
        add(*item)
    return signals


def _pyproject_tools(path: Path) -> list[tuple[str, str, str]]:
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return []
    found: list[tuple[str, str, str]] = []
    tool = data.get("tool")
    if isinstance(tool, dict):
        for section in sorted(tool):
            name = _KNOWN_TOOL_SECTIONS.get(section)
            if name:
                found.append((name, f"pyproject.toml [tool.{section}]", SOURCE_CONFIG))
    build_system = data.get("build-system")
    if isinstance(build_system, dict):
        requires = build_system.get("requires")
        if isinstance(requires, list):
            for item in requires:
                if isinstance(item, str) and item.strip():
                    name = _requirement_name(item)
                    if name in _KNOWN_TOOL_SECTIONS:
                        found.append(
                            (
                                _KNOWN_TOOL_SECTIONS[name],
                                f"pyproject.toml [build-system] requires = "
                                f"{item.strip()}",
                                SOURCE_CONFIG,
                            )
                        )
    return found


def _requirement_tools(root: Path) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for filename in ("requirements.txt", "requirements-dev.txt"):
        path = root / filename
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            name = _requirement_name(line)
            if name in _KNOWN_TOOL_SECTIONS:
                found.append(
                    (
                        _KNOWN_TOOL_SECTIONS[name],
                        f"{filename}: {line}",
                        SOURCE_FILE,
                    )
                )
    return found


def _documented_tools(root: Path) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for command in readme.scan_readme_commands(root):
        for token in command.command.replace("|", " ").split():
            name = _DOCUMENTED_TOOLS.get(token.lower())
            if name:
                found.append(
                    (
                        name,
                        f"{command.file}:{command.line} ({command.command})",
                        SOURCE_DOCS,
                    )
                )
    return found
