"""Extraction of commands documented in README files.

This scanner is purely textual: it never runs, imports or resolves anything
found in the documentation.
"""

from __future__ import annotations

import re
from pathlib import Path

from reprocheck.models import ReadmeCommand

READMES = ("README.md", "README.rst")

# First tokens that mark a line as a command worth recording.
COMMAND_TOKENS = frozenset(
    {
        "pip",
        "pip3",
        "pipenv",
        "python",
        "python3",
        "py",
        "pytest",
        "uv",
        "uvx",
        "poetry",
        "conda",
        "mamba",
        "micromamba",
        "make",
        "tox",
        "nox",
        "hatch",
        "pyenv",
        "virtualenv",
        "python3.11",
        "python3.12",
        "python3.13",
    }
)

_FENCE_RE = re.compile(r"^(?P<indent>\s*)(?P<fence>`{3,}|~{3,})\s*(?P<info>.*)$")
_LITERAL_BLOCK_RE = re.compile(r"(?::|::)\s*$")
_DIRECTIVE_RE = re.compile(r"^\.\.\s+code-block::\s*(?P<lang>[\w+-]*)\s*$")
_SPLIT_RE = re.compile(r"[\s;|&()<>]+")
_PROMPT_RE = re.compile(r"^[$#>]\s+")


def scan_readme_commands(root: Path) -> list[ReadmeCommand]:
    """Extract documented commands from the first README found in ``root``."""
    for name in READMES:
        path = root / name
        if path.is_file():
            return list(_scan_file(path, name))
    return []


def _scan_file(path: Path, relative: str) -> list[ReadmeCommand]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    commands: list[ReadmeCommand] = []
    fence: str | None = None
    fence_info: str | None = None
    literal_indent: int | None = None
    literal_language: str | None = None

    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()

        if fence is not None:
            match = _FENCE_RE.match(raw)
            if (
                match
                and match.group("fence")[0] == fence[0]
                and len(match.group("fence")) >= len(fence)
                and not match.group("info").strip()
            ):
                fence = None
                fence_info = None
            else:
                _append(commands, raw, relative, number, fence_info)
            continue

        if literal_indent is not None:
            if not stripped:
                continue
            indent = len(raw) - len(raw.lstrip())
            if indent >= literal_indent:
                _append(commands, raw, relative, number, literal_language)
                continue
            literal_indent = None
            literal_language = None

        match = _FENCE_RE.match(raw)
        if match:
            fence = match.group("fence")
            fence_info = match.group("info").strip() or None
            continue

        directive = _DIRECTIVE_RE.match(stripped)
        if directive or (
            _LITERAL_BLOCK_RE.search(raw) and not stripped.startswith("..[")
        ):
            literal_indent = 1
            literal_language = directive.group("lang") if directive else None

    return commands


def _append(
    commands: list[ReadmeCommand],
    raw: str,
    relative: str,
    number: int,
    language: str | None,
) -> None:
    line = raw.strip()
    if not line or line.startswith("#"):
        return
    line = _PROMPT_RE.sub("", line)
    if not line:
        return
    if not any(token in COMMAND_TOKENS for token in _SPLIT_RE.split(line)):
        return
    commands.append(
        ReadmeCommand(
            command=" ".join(line.split()),
            file=relative,
            line=number,
            block_language=language,
        )
    )
