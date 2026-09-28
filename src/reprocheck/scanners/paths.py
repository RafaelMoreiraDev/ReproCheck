"""Static detection of local filesystem references.

This scanner never opens, imports or executes anything from the analysed
project. It only reads text files and reports literal references.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from reprocheck.facts import AbsolutePathRef, FileReference
from reprocheck.scanners.manifest import SKIP_DIRECTORIES

# File types inspected for hard-coded paths.
SCANNED_SUFFIXES = frozenset({".py", ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg"})

# Files larger than this are not inspected (generated or data files).
MAX_FILE_BYTES = 512 * 1024

# Directories that only hold vendored or generated content.
SKIP_PATH_PARTS = SKIP_DIRECTORIES | {
    ".eggs",
    "htmlcov",
    "examples",
    "example",
    "notebooks",
}

# ``C:\Users\me`` or ``c:/Users/me``.
_WINDOWS_ABS_RE = re.compile(r"(?<![\w])(?P<value>[A-Za-z]:[\\/][^\s\"'<>|,;)\]]*)")
# ``/home/me`` or ``/Users/me`` (macOS).
_UNIX_HOME_RE = re.compile(r"(?<![\w/])(?P<value>/(?:home|Users)/[^\s\"'<>|,;)\]]*)")

# File-reading calls with a literal path argument.
#
# The call name must not be the tail of another identifier: ``yaml.safe_load``
# and ``Path.joinpath`` are not file reads of their first argument. A dot is
# allowed before the name, so ``np.load`` and ``io.open`` still match.
#
# ``Path`` itself is deliberately absent: it is a path *constructor*, and
# ``Path("out.png").unlink()`` says nothing about a missing input file.
_CALL_RE = re.compile(
    r"""(?<![A-Za-z0-9_])(?P<call>open|read_csv|read_json|read_parquet|read_excel"""
    r"""|load|loadtxt|loadmat)"""
    r"""\s*\(\s*(?P<quote>["'])(?P<value>[^"'\n]+)(?P=quote)""",
    re.IGNORECASE,
)

# Values that are not repository-relative filesystem paths.
_NOT_A_PATH_RE = re.compile(r"[$~<>*?{}|;]|^https?://|^file://|://")


def _is_comment_line(line: str) -> bool:
    """Return ``True`` for a line whose first characters are a comment marker.

    Commented-out code is not executed, so a literal inside it is not a
    reference the project makes. The marker has to be the first non-space
    character, which keeps a ``#`` inside a string on the same line.
    """
    stripped = line.lstrip()
    return stripped.startswith("#") or stripped.startswith("//")


def scan_paths(root: Path) -> tuple[list[AbsolutePathRef], list[FileReference]]:
    """Return hard-coded absolute paths and literal file references."""
    absolute: list[AbsolutePathRef] = []
    references: list[FileReference] = []

    for relative, absolute_path in _iter_text_files(root):
        try:
            text = absolute_path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        if "\x00" in text:
            continue
        absolute.extend(_absolute_refs(relative, text))
        references.extend(_file_refs(root, absolute_path, relative, text))

    return (absolute, references)


def _iter_text_files(root: Path):
    for current, dirnames, filenames in os.walk(root, onerror=None):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in SKIP_DIRECTORIES and d not in SKIP_PATH_PARTS
        )
        for name in sorted(filenames):
            path = Path(current) / name
            if path.suffix.lower() not in SCANNED_SUFFIXES:
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield (path.relative_to(root).as_posix(), path)


def _absolute_refs(relative: str, text: str) -> list[AbsolutePathRef]:
    found: list[AbsolutePathRef] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for style, pattern in (
            ("windows", _WINDOWS_ABS_RE),
            ("unix-home", _UNIX_HOME_RE),
        ):
            for match in pattern.finditer(line):
                value = match.group("value").rstrip(".,;:")
                # Braces are a template placeholder, never a path: found while
                # validating on real projects, ``didn't:\n{key}`` in a message
                # was reported as a Windows drive.
                if "{" in value or "}" in value:
                    continue
                if not _has_path_tail(value):
                    continue
                found.append(AbsolutePathRef(value, style, relative, number))
    return found


def _has_path_tail(value: str) -> bool:
    """Require a real path component right after the drive or home prefix.

    A single character is not a name: found while validating on real projects,
    the apostrophe of a prose contraction (``didn't:\\n``) and a format
    placeholder (``%s:\\n``) were both read as a Windows drive, because the
    character that follows the colon is an escape, not a directory.
    """
    stripped = re.sub(r"^[A-Za-z]:[\\/]|^/(?:home|Users)/", "", value)
    segments = [item for item in re.split(r"[\\/]", stripped) if item]
    return bool(segments) and len(segments[0]) >= 2


def _file_refs(root: Path, path: Path, relative: str, text: str) -> list[FileReference]:
    found: list[FileReference] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if _is_comment_line(line):
            continue
        for match in _CALL_RE.finditer(line):
            call = match.group("call")
            if not _is_reading_call(line, match, call):
                continue
            value = match.group("value").strip()
            if not _is_repository_relative(value):
                continue
            resolved = _resolve(root, path, value)
            exists = _exists(root, path, value)
            found.append(
                FileReference(
                    call=call,
                    value=value,
                    file=relative,
                    line=number,
                    resolved=str(resolved) if resolved is not None else None,
                    exists=exists,
                )
            )
    return found


def _is_reading_call(line: str, match: re.Match[str], call: str) -> bool:
    """Return ``True`` when the call really reads a file.

    Two shapes are rejected, both found while validating on real projects:

    ``viewer.open('x.tif')``
        a project API that happens to be called ``open``. Only the builtin and
        ``io.open`` read, because every reader method (``pd.read_csv``,
        ``np.load``) is normally reached through its module;
    ``open('rv.input', 'w')``
        a file being **written**, not read.
    """
    if call.lower() == "open":
        start = match.start("call")
        if (
            line[start - 1 : start] == "."
            and line[max(0, start - 3) : start - 1] != "io"
        ):
            return False
        rest = line[match.end() :]
        mode = re.match(r"""\s*,\s*(?P<quote>["'])(?P<mode>[^"']*)(?P=quote)""", rest)
        if mode and set(mode.group("mode")) & set("wax+"):
            return False
    return True


def _is_repository_relative(value: str) -> bool:
    if not value or _NOT_A_PATH_RE.search(value):
        return False
    if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("/"):
        return False
    if value.endswith(("/", "\\")):
        return False
    # Require something that looks like a filename or a nested path.
    return bool(re.search(r"[\\/]|\.[A-Za-z0-9]{1,8}$", value))


def _resolve(root: Path, source: Path, value: str) -> Path | None:
    """Resolve ``value`` against the source file's folder, then the project."""
    return next(
        (
            candidate
            for candidate in _candidates(root, source, value)
            if candidate.exists()
        ),
        None,
    )


def _exists(root: Path, source: Path, value: str) -> bool:
    return any(candidate.exists() for candidate in _candidates(root, source, value))


def _candidates(root: Path, source: Path, value: str) -> list[Path]:
    return [(source.parent / value).resolve(), (root / value).resolve()]
