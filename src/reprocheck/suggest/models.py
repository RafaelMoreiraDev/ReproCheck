"""Typed model of a fix suggestion.

A suggestion is a **proposal**, never an action. It carries the before and the
after so a reader can see the whole change, and it is honest about its own
limits: ``limitations`` says what the proposal does not know.

Three answers are possible, and the engine must always give one of them:

``FIX_AVAILABLE``
    there is an unambiguous, local transformation (``safety`` is ``SAFE``);
``REVIEW_REQUIRED``
    a direction exists but a decision or a missing fact does too
    (``safety`` is ``REVIEW_REQUIRED``);
``NO_AUTOFIX``
    ReproCheck can explain the problem and must not edit
    (``safety`` is ``MANUAL_ONLY``).

No suggestion is ever applied in this version. There is no ``reprocheck fix``,
no ``--apply`` and no prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

#: Version of the ``*.suggestions.json`` document itself.
SUGGESTION_SCHEMA_VERSION = "1"


class Safety(StrEnum):
    """How safe it would be to apply the proposal, if anyone applied it."""

    SAFE = "SAFE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    MANUAL_ONLY = "MANUAL_ONLY"

    def to_dict(self) -> str:
        return self.value


class SuggestionKind(StrEnum):
    """The three answers the engine is allowed to give."""

    FIX_AVAILABLE = "FIX_AVAILABLE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NO_AUTOFIX = "NO_AUTOFIX"

    def to_dict(self) -> str:
        return self.value


#: The kind each safety level implies. There is no fourth combination.
KIND_BY_SAFETY = {
    Safety.SAFE: SuggestionKind.FIX_AVAILABLE,
    Safety.REVIEW_REQUIRED: SuggestionKind.REVIEW_REQUIRED,
    Safety.MANUAL_ONLY: SuggestionKind.NO_AUTOFIX,
}


@dataclass(frozen=True, slots=True)
class FixSuggestion:
    """One deterministic proposal attached to one finding.

    ``before_sha256`` and ``after_sha256`` are the preconditions an application
    would need: the exact bytes of the target file before the proposal, and the
    exact bytes of ``after``. They are hashes of **bytes**, not of the parsed
    text, so an encoding or newline difference is part of the contract.
    """

    finding_id: str
    title: str
    safety: Safety
    description: str
    rationale: str
    file: str | None = None
    confidence: str = "medium"
    before: str | None = None
    after: str | None = None
    before_sha256: str | None = None
    after_sha256: str | None = None
    unified_diff: str | None = None
    limitations: tuple[str, ...] = ()
    suggestion_id: str = ""

    @property
    def kind(self) -> SuggestionKind:
        return KIND_BY_SAFETY[self.safety]

    @property
    def has_patch(self) -> bool:
        return self.unified_diff is not None

    @property
    def requires_user_approval(self) -> bool:
        """Every proposal is a proposal: nothing here runs without a human."""
        return True

    @property
    def can_auto_apply_later(self) -> bool:
        """Only an unambiguous, reversible, local edit could ever be applied."""
        return (
            self.safety is Safety.SAFE
            and self.unified_diff is not None
            and self.after is not None
            and self.before_sha256 is not None
            and self.after_sha256 is not None
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "suggestion_id": self.suggestion_id,
            "finding_id": self.finding_id,
            "kind": self.kind.value,
            "title": self.title,
            "safety": self.safety.value,
            "confidence": self.confidence,
            "file": self.file,
            "description": self.description,
            "rationale": self.rationale,
            "before": self.before,
            "after": self.after,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "unified_diff": self.unified_diff,
            "requires_user_approval": self.requires_user_approval,
            "can_auto_apply_later": self.can_auto_apply_later,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class SkippedFinding:
    """A finding the engine deliberately produced no proposal for."""

    finding_id: str
    reason: str
    severity: str = "info"
    file: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "finding_id": self.finding_id,
            "severity": self.severity,
            "file": self.file,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SuggestionSummary:
    """How many findings fell into each bucket."""

    findings: int = 0
    fix_available: int = 0
    review_required: int = 0
    manual_only: int = 0
    patches: int = 0
    no_proposal: int = 0
    informational: int = 0

    def to_dict(self) -> dict[str, str | int]:
        return {
            "findings": self.findings,
            "fix_available": self.fix_available,
            "review_required": self.review_required,
            "manual_only": self.manual_only,
            "patches": self.patches,
            "no_proposal": self.no_proposal,
            "informational": self.informational,
        }


@dataclass(frozen=True, slots=True)
class SuggestionReport:
    """The whole ``reprocheck suggest`` result."""

    reprocheck_version: str
    scan_timestamp: str
    project: dict[str, object] = field(default_factory=dict)
    suggestions: tuple[FixSuggestion, ...] = ()
    skipped: tuple[SkippedFinding, ...] = ()
    summary: SuggestionSummary = field(default_factory=SuggestionSummary)

    @property
    def safe(self) -> tuple[FixSuggestion, ...]:
        return tuple(item for item in self.suggestions if item.safety is Safety.SAFE)

    @property
    def review(self) -> tuple[FixSuggestion, ...]:
        return tuple(
            item for item in self.suggestions if item.safety is Safety.REVIEW_REQUIRED
        )

    @property
    def manual(self) -> tuple[FixSuggestion, ...]:
        return tuple(
            item for item in self.suggestions if item.safety is Safety.MANUAL_ONLY
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "suggestion_schema_version": SUGGESTION_SCHEMA_VERSION,
            "reprocheck_version": self.reprocheck_version,
            "scan_timestamp": self.scan_timestamp,
            "project": self.project,
            "summary": self.summary.to_dict(),
            "suggestions": [item.to_dict() for item in self.suggestions],
            "no_proposal": [item.to_dict() for item in self.skipped],
        }
