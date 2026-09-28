"""Extraction of commands documented in README files.

This scanner is purely textual: it never runs, imports or resolves anything
found in the documentation.
"""

from __future__ import annotations

import re
from pathlib import Path

from reprocheck.facts import ReadmeReference
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
        "cd",
        "git",
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
    return [command for block in _scan_readme(root) for command in block]


def _scan_readme(root: Path) -> list[list[ReadmeCommand]]:
    """Return the documented commands grouped by code block."""
    for name in READMES:
        path = root / name
        if path.is_file():
            return _scan_blocks(path, name)
    return []


# Reference kinds extracted from documented commands.
KIND_REQUIREMENTS = "requirements"
KIND_SCRIPT = "script"
KIND_TEST_PATH = "test-path"
KIND_DIRECTORY = "directory"

_PYTHON_TOKENS = frozenset(
    {"python", "python3", "py", "python3.11", "python3.12", "python3.13"}
)
_UNSAFE_VALUE_RE = re.compile(r"[$~<>*?{}|;\"']|^\.|^\.\.$")
_CLONE_COMMAND_RE = re.compile(r"^git\s+clone\b", re.IGNORECASE)


def scan_readme_references(root: Path) -> list[ReadmeReference]:
    """Extract filesystem references from the documented commands.

    Directory references are skipped inside a code block that clones the
    repository: those directories are created by the clone itself, not by the
    repository content.
    """
    found: list[ReadmeReference] = []
    for block in _scan_readme(root):
        clones = any(_CLONE_COMMAND_RE.match(command.command) for command in block)
        for command in block:
            found.extend(_references_for(command, allow_directory=not clones))
    return found


def references_from_commands(
    commands: list[ReadmeCommand],
) -> list[ReadmeReference]:
    """Return the filesystem paths referenced by ``commands``."""
    found: list[ReadmeReference] = []
    for item in commands:
        found.extend(_references_for(item))
    return found


def _references_for(
    command: ReadmeCommand, allow_directory: bool = True
) -> list[ReadmeReference]:
    tokens = _SPLIT_RE.split(command.command.strip())
    if not tokens:
        return []
    head = tokens[0].lower()
    rest = tokens[1:]

    def make(kind: str, value: str) -> ReadmeReference:
        return ReadmeReference(
            kind=kind,
            value=value,
            command=command.command,
            file=command.file,
            line=command.line,
        )

    if head in {"pip", "pip3"}:
        return [r for r in _pip_references(rest, make) if r is not None]
    if head in _PYTHON_TOKENS:
        if rest and rest[0] == "-m":
            return []
        for token in rest:
            if token.startswith("-"):
                continue
            return [make(KIND_SCRIPT, token)] if _is_path_like(token) else []
        return []
    if head == "pytest":
        for token in rest:
            if token.startswith("-"):
                continue
            return [make(KIND_TEST_PATH, token)] if _is_directory_like(token) else []
        return []
    if head == "cd":
        if allow_directory and rest and _is_directory_like(rest[0]):
            return [make(KIND_DIRECTORY, rest[0])]
        return []
    return []


def _pip_references(rest: list[str], make) -> list[ReadmeReference | None]:
    references: list[ReadmeReference | None] = []
    expecting = False
    for token in rest:
        if expecting:
            if _is_path_like(token):
                references.append(make(KIND_REQUIREMENTS, token))
            expecting = False
            continue
        if token in {"-r", "--requirement"}:
            expecting = True
            continue
        if token.startswith("-"):
            continue
        if _is_path_like(token):
            references.append(make(KIND_REQUIREMENTS, token))
    return references


def _is_path_like(value: str) -> bool:
    """Return ``True`` for values that look like repository-relative paths."""
    if not _is_safe_value(value):
        return False
    return bool(re.search(r"[\\/]", value) or re.search(r"\.[A-Za-z0-9]{1,8}$", value))


def _is_directory_like(value: str) -> bool:
    """Return ``True`` for a plain directory name or a relative directory."""
    return bool(value) and _is_safe_value(value)


def _is_safe_value(value: str) -> bool:
    """Reject globs, environment variables, URLs and the current directory.

    A URL is rejected here as well: ``pip install --upgrade
    https://host/project/archive/main.zip`` documents an install, not a file
    that should be in the repository.
    """
    if not value or _UNSAFE_VALUE_RE.search(value):
        return False
    if "://" in value:
        return False
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
        return False
    return True


def _scan_blocks(path: Path, relative: str) -> list[list[ReadmeCommand]]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []

    blocks: list[list[ReadmeCommand]] = []
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
                if commands:
                    blocks.append(commands)
                    commands = []
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
            if commands:
                blocks.append(commands)
                commands = []

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

    if commands:
        blocks.append(commands)
    return blocks


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
