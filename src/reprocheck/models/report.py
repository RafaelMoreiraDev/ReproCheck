"""Top-level report model and the JSON document produced by ReproCheck."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from reprocheck.models.detected import (
    DetectedFile,
    PackageManagerHint,
    PythonRequirement,
    ReadmeCommand,
)
from reprocheck.models.finding import Finding
from reprocheck.models.git import GitInfo
from reprocheck.models.project import ProjectScan


def _sorted(items: Iterable[object], *keys: str) -> list[object]:
    def sort_key(item: object) -> tuple[object, ...]:
        data = item.to_dict()  # type: ignore[attr-defined]
        return tuple(str(data.get(key) or "") for key in keys)

    return sorted(items, key=sort_key)


@dataclass(slots=True)
class ScanReport:
    """The complete result of one ``reprocheck scan`` run."""

    reprocheck_version: str
    scan_timestamp: str
    project: ProjectScan
    git: GitInfo = field(default_factory=GitInfo)
    detected_files: list[DetectedFile] = field(default_factory=list)
    python_requirements: list[PythonRequirement] = field(default_factory=list)
    package_manager_hints: list[PackageManagerHint] = field(default_factory=list)
    readme_commands: list[ReadmeCommand] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    report_schema_version: str = "1"
    facts: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic, JSON-serialisable representation."""
        return {
            "reprocheck_version": self.reprocheck_version,
            "report_schema_version": self.report_schema_version,
            "scan_timestamp": self.scan_timestamp,
            "project": self.project.to_dict(),
            "git": self.git.to_dict(),
            "detected_files": [
                item.to_dict()
                for item in _sorted(self.detected_files, "category", "path")
            ],
            "python_requirements": [
                item.to_dict()
                for item in _sorted(
                    self.python_requirements, "source", "value", "file", "line"
                )
            ],
            "package_manager_hints": [
                item.to_dict()
                for item in _sorted(self.package_manager_hints, "manager", "evidence")
            ],
            "readme_commands": [
                item.to_dict()
                for item in _sorted(self.readme_commands, "file", "line", "command")
            ],
            "findings": [
                item.to_dict()
                for item in _sorted(self.findings, "id", "category", "message")
            ],
            "facts": self.facts,
        }
