"""Objective findings produced by the built-in checks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    """Severity of a finding."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    def to_dict(self) -> str:
        return self.value


class Confidence(StrEnum):
    """How certain a finding is, based on objective criteria only."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    def to_dict(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Finding:
    """A single objective observation about the scanned project."""

    id: str
    title: str
    severity: Severity
    category: str
    message: str
    evidence: str | None = None
    file: str | None = None
    line: int | None = None
    confidence: Confidence = Confidence.MEDIUM

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "severity": self.severity.value,
            "category": self.category,
            "message": self.message,
            "evidence": self.evidence,
            "file": self.file,
            "line": self.line,
            "confidence": self.confidence.value,
        }
