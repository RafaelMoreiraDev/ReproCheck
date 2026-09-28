"""Typed model of one fix application.

The status is a single word, not a boolean, because the interesting cases are
exactly the ones a boolean would flatten:

``DRY_RUN``          the proposal was shown and nothing was written
``APPLIED``          the bytes were replaced, validated and the finding is gone
``STALE``            the target file changed since the proposal was generated
``NOT_APPLICABLE``   the suggestion is gone, is not SAFE, or has no patch
``FAILED``           something went wrong and the bytes were restored
``ROLLED_BACK``      the write was reverted, so the project is as it was
``ROLLBACK_FAILED``  the write could not be reverted: a severe ReproCheck error

No status other than ``APPLIED`` means "the project now contains the change".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

#: Version of the ``*.fix.json`` document itself.
FIX_SCHEMA_VERSION = "1"


class ApplicationStatus(StrEnum):
    """How one requested application ended."""

    DRY_RUN = "DRY_RUN"
    APPLIED = "APPLIED"
    STALE = "STALE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"

    def to_dict(self) -> str:
        return self.value


class RollbackState(StrEnum):
    """Whether the bytes were restored, and whether anybody asked."""

    NOT_NEEDED = "NOT_NEEDED"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED_STALE = "SKIPPED_STALE"

    def to_dict(self) -> str:
        return self.value


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass(frozen=True, slots=True)
class GitObservation:
    """The Git state around an operation, read with read-only commands."""

    is_repository: bool = False
    head: str | None = None
    status: str | None = None
    changed_paths: tuple[str, ...] = ()
    target_already_modified: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "is_repository": self.is_repository,
            "head": self.head,
            "status": self.status,
            "changed_paths": list(self.changed_paths),
            "target_already_modified": self.target_already_modified,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class Validation:
    """What was checked after the write, and what came out of it."""

    after_hash_matches: bool = False
    finding_resolved: bool = False
    performed: bool = False
    detail: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "performed": self.performed,
            "after_hash_matches": self.after_hash_matches,
            "finding_resolved": self.finding_resolved,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class FixApplicationResult:
    """Everything one ``reprocheck fix`` invocation did, or refused to do."""

    status: ApplicationStatus
    reprocheck_version: str
    project: dict[str, object] = field(default_factory=dict)
    requested: str | None = None
    suggestion_id: str | None = None
    finding_id: str | None = None
    safety: str | None = None
    file: str | None = None
    dry_run: bool = False
    applied: bool = False
    before_sha256: str | None = None
    after_sha256: str | None = None
    unified_diff: str | None = None
    reason: str | None = None
    messages: tuple[str, ...] = ()
    precondition_passed: bool | None = None
    backup_path: str | None = None
    record_id: str | None = None
    record_path: str | None = None
    rollback: RollbackState = RollbackState.NOT_NEEDED
    rollback_reason: str | None = None
    validation: Validation = field(default_factory=Validation)
    git_before: GitObservation = field(default_factory=GitObservation)
    git_after: GitObservation = field(default_factory=GitObservation)
    unexpected_paths: tuple[str, ...] = ()
    applied_at: str = field(default_factory=utc_now)
    findings_before: int = 0
    findings_after: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "fix_schema_version": FIX_SCHEMA_VERSION,
            "status": self.status.value,
            "reprocheck_version": self.reprocheck_version,
            "applied_at": self.applied_at,
            "project": self.project,
            "requested": self.requested,
            "suggestion_id": self.suggestion_id,
            "finding_id": self.finding_id,
            "safety": self.safety,
            "file": self.file,
            "dry_run": self.dry_run,
            "applied": self.applied,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "unified_diff": self.unified_diff,
            "precondition_passed": self.precondition_passed,
            "reason": self.reason,
            "messages": list(self.messages),
            "validation": self.validation.to_dict(),
            "rollback": self.rollback.value,
            "rollback_reason": self.rollback_reason,
            "backup_path": self.backup_path,
            "record_id": self.record_id,
            "record_path": self.record_path,
            "git_before": self.git_before.to_dict(),
            "git_after": self.git_after.to_dict(),
            "unexpected_paths": list(self.unexpected_paths),
            "findings_before": self.findings_before,
            "findings_after": self.findings_after,
        }


@dataclass(frozen=True, slots=True)
class ApplicationRecord:
    """The auditable record of one real write. One JSON file per operation."""

    record_id: str
    project_identity: str
    project_path: str
    suggestion_id: str
    finding_id: str
    safety: str
    file: str
    applied_at: str
    before_sha256: str
    after_sha256: str
    backup_path: str
    dry_run: bool
    success: bool
    rollback: RollbackState
    reprocheck_version: str
    status: ApplicationStatus
    reason: str | None = None
    rollback_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "project_identity": self.project_identity,
            "project_path": self.project_path,
            "suggestion_id": self.suggestion_id,
            "finding_id": self.finding_id,
            "safety": self.safety,
            "file": self.file,
            "applied_at": self.applied_at,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "backup_path": self.backup_path,
            "dry_run": self.dry_run,
            "success": self.success,
            "rollback": self.rollback.value,
            "rollback_reason": self.rollback_reason,
            "status": self.status.value,
            "reason": self.reason,
            "reprocheck_version": self.reprocheck_version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ApplicationRecord:
        return cls(
            record_id=str(data["record_id"]),
            project_identity=str(data.get("project_identity", "")),
            project_path=str(data.get("project_path", "")),
            suggestion_id=str(data.get("suggestion_id", "")),
            finding_id=str(data.get("finding_id", "")),
            safety=str(data.get("safety", "")),
            file=str(data.get("file", "")),
            applied_at=str(data.get("applied_at", "")),
            before_sha256=str(data.get("before_sha256", "")),
            after_sha256=str(data.get("after_sha256", "")),
            backup_path=str(data.get("backup_path", "")),
            dry_run=bool(data.get("dry_run", False)),
            success=bool(data.get("success", False)),
            rollback=RollbackState(
                data.get("rollback", RollbackState.NOT_NEEDED.value)
            ),
            reprocheck_version=str(data.get("reprocheck_version", "")),
            status=ApplicationStatus(data.get("status", "FAILED")),
            reason=data.get("reason"),
            rollback_reason=data.get("rollback_reason"),
        )
