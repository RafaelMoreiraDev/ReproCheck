"""Models describing what was found inside the scanned project."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DetectedFile:
    """A tracked file or directory that is present (or explicitly missing).

    ``present`` is always ``True`` for entries included in a report: only
    existing paths are recorded. Use :attr:`kind` to distinguish files from
    directories.
    """

    path: str
    kind: str  # "file" | "directory"
    category: str  # e.g. "python-config", "docs", "vcs", "tests", "ci"

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "kind": self.kind, "category": self.category}


@dataclass(frozen=True, slots=True)
class PythonRequirement:
    """A Python version requirement declared by some source.

    Conflicting values from different sources are never reconciled in V0.1;
    each declaration is reported verbatim.
    """

    source: str
    value: str
    file: str | None = None
    line: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "value": self.value,
            "file": self.file,
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class PackageManagerHint:
    """A signal that a Python packaging tool is used by the project."""

    manager: str
    evidence: str

    def to_dict(self) -> dict[str, object]:
        return {"manager": self.manager, "evidence": self.evidence}


@dataclass(frozen=True, slots=True)
class ReadmeCommand:
    """A command line extracted from a Markdown/reST code block.

    The command is recorded only. ReproCheck never executes it.
    """

    command: str
    file: str
    line: int
    block_language: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "file": self.file,
            "line": self.line,
            "block_language": self.block_language,
        }
