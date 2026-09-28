"""Typed model of a baseline comparison.

A diff is a list of observed changes, never an interpretation: it says that a
fact changed, not that the project got better or worse. There is no score, no
ranking and no recommendation here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

#: Version of the ``*.diff.json`` document itself.
DIFF_SCHEMA_VERSION = "1"


class ChangeKind(StrEnum):
    """How a fact changed."""

    ADDED = "added"
    RESOLVED = "resolved"
    CHANGED = "changed"

    def to_dict(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ReportIdentity:
    """Enough to recognise which report this is."""

    name: str | None = None
    path: str | None = None
    head: str | None = None
    report_schema_version: str | None = None
    reprocheck_version: str | None = None
    scan_timestamp: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "path": self.path,
            "head": self.head,
            "report_schema_version": self.report_schema_version,
            "reprocheck_version": self.reprocheck_version,
            "scan_timestamp": self.scan_timestamp,
        }

    @classmethod
    def from_report(cls, report: dict) -> ReportIdentity:
        project = report.get("project") or {}
        git = report.get("git") or {}
        return cls(
            name=project.get("name"),
            path=project.get("path"),
            head=git.get("head"),
            report_schema_version=report.get("report_schema_version"),
            reprocheck_version=report.get("reprocheck_version"),
            scan_timestamp=report.get("scan_timestamp"),
        )


@dataclass(frozen=True, slots=True)
class VerdictChange:
    """``previous`` and ``current``, with no automatic judgement attached."""

    previous: str | None
    current: str | None
    changed: bool
    note: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "previous": self.previous,
            "current": self.current,
            "changed": self.changed,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class FindingChange:
    """One finding that appeared, disappeared or changed."""

    kind: ChangeKind
    identity: str
    previous: dict | None = None
    current: dict | None = None
    changed_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "identity": self.identity,
            "previous": self.previous,
            "current": self.current,
            "changed_fields": list(self.changed_fields),
        }


@dataclass(frozen=True, slots=True)
class FactChange:
    """One material change in a fact domain (dependency, Python, CI, run)."""

    domain: str
    key: str
    label: str
    kind: ChangeKind
    previous: str | None = None
    current: str | None = None
    changed_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "key": self.key,
            "label": self.label,
            "kind": self.kind.value,
            "previous": self.previous,
            "current": self.current,
            "changed_fields": list(self.changed_fields),
        }


@dataclass(frozen=True, slots=True)
class FindingChanges:
    """Findings grouped by how they changed."""

    added: tuple[FindingChange, ...] = ()
    resolved: tuple[FindingChange, ...] = ()
    changed: tuple[FindingChange, ...] = ()

    @property
    def total(self) -> int:
        return len(self.added) + len(self.resolved) + len(self.changed)

    def to_dict(self) -> dict[str, object]:
        return {
            "added": [item.to_dict() for item in self.added],
            "resolved": [item.to_dict() for item in self.resolved],
            "changed": [item.to_dict() for item in self.changed],
        }


@dataclass(frozen=True, slots=True)
class ReproducibilityDiff:
    """The whole comparison of two reports."""

    baseline: ReportIdentity
    current: ReportIdentity
    verdict: VerdictChange
    findings: FindingChanges = field(default_factory=FindingChanges)
    dependencies: tuple[FactChange, ...] = ()
    python: tuple[FactChange, ...] = ()
    ci: tuple[FactChange, ...] = ()
    reproduction: tuple[FactChange, ...] = ()

    @property
    def material_change_count(self) -> int:
        return (
            (1 if self.verdict.changed else 0)
            + self.findings.total
            + len(self.dependencies)
            + len(self.python)
            + len(self.ci)
            + len(self.reproduction)
        )

    @property
    def has_changes(self) -> bool:
        return self.material_change_count > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "diff_schema_version": DIFF_SCHEMA_VERSION,
            "baseline": self.baseline.to_dict(),
            "current": self.current.to_dict(),
            "verdict_change": self.verdict.to_dict(),
            "findings": self.findings.to_dict(),
            "dependencies": [item.to_dict() for item in self.dependencies],
            "python": [item.to_dict() for item in self.python],
            "ci": [item.to_dict() for item in self.ci],
            "reproduction": [item.to_dict() for item in self.reproduction],
            "material_change_count": self.material_change_count,
        }
