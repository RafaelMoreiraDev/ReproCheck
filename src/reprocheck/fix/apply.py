"""Applying one SAFE suggestion, with preconditions, a backup and a rollback.

The contract of this module, in order:

1. regenerate the suggestions from the project **now** and find the requested
   one; a suggestion from an older report is never trusted;
2. accept it only if it is ``SAFE``, has a diff and is marked as applicable;
3. in ``--apply``, re-read the target as bytes and compare their SHA-256 with
   the precondition the proposal carries. A different hash means somebody else
   wrote to the file: stop, do not merge, do not overwrite;
4. copy the exact bytes to a backup **outside** the project;
5. write through a temporary file in the same directory, ``fsync`` and
   ``os.replace``, preserving the file mode;
6. re-read the bytes and require the expected hash;
7. re-scan statically and require the finding to be gone;
8. if 5, 6 or 7 failed, restore the backup and verify the restoration.

Nothing here is a formatter and nothing here rebuilds a file: the bytes written
are exactly the bytes the rule proposed.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from reprocheck import __version__
from reprocheck.fix import git as git_observer
from reprocheck.fix.models import (
    ApplicationRecord,
    ApplicationStatus,
    FixApplicationResult,
    RollbackState,
    Validation,
    utc_now,
)
from reprocheck.output import project_identity, project_output_dir
from reprocheck.scanner import build_report, collect_facts, resolve_target
from reprocheck.suggest import Safety, suggest

#: Suffix of the temporary file created next to the target.
TEMP_SUFFIX = ".reprocheck-tmp"

BACKUP_DIRNAME = "applied"
RECORD_PREFIX = "applied-"

REJECTED = (
    "This suggestion requires human judgment and cannot be applied automatically."
)


class FixError(Exception):
    """Raised for an operational problem that is not a status of its own."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #


def run_fix(
    project: str | Path,
    suggestion_id: str,
    *,
    apply: bool = False,
    state_dir: Path | None = None,
) -> FixApplicationResult:
    """Show, or apply, exactly one suggestion. Dry run unless ``apply``."""
    root = resolve_target(project)
    facts = collect_facts(root)
    report = build_report(facts)
    proposals = suggest(facts, report)
    base = dict(
        reprocheck_version=__version__,
        project=report.project.to_dict(),
        requested=suggestion_id,
        dry_run=not apply,
        git_before=git_observer.observe(root),
        findings_before=len(report.findings),
    )

    suggestion = next(
        (item for item in proposals.suggestions if item.suggestion_id == suggestion_id),
        None,
    )
    if suggestion is None:
        return FixApplicationResult(
            status=ApplicationStatus.NOT_APPLICABLE,
            reason=(
                f"the suggestion {suggestion_id} does not exist any more; the "
                f"findings of this project produce "
                f"{len(proposals.suggestions)} suggestion(s) now. Run "
                f"`reprocheck suggest` again to see the current list"
            ),
            messages=("Nothing was written.",),
            **base,  # type: ignore[arg-type]
        )

    identified = {
        **base,
        "suggestion_id": suggestion.suggestion_id,
        "finding_id": suggestion.finding_id,
        "safety": suggestion.safety.value,
        "file": suggestion.file,
        "unified_diff": suggestion.unified_diff,
    }

    refusal = _refusal(suggestion, suggestion_id)
    if refusal is not None:
        return FixApplicationResult(
            status=ApplicationStatus.NOT_APPLICABLE,
            reason=refusal,
            messages=("Nothing was written.",),
            **identified,  # type: ignore[arg-type]
        )

    target = root / str(suggestion.file)
    if not target.is_file():
        return FixApplicationResult(
            status=ApplicationStatus.NOT_APPLICABLE,
            reason=f"the target file {suggestion.file} does not exist",
            messages=("Nothing was written.",),
            **identified,  # type: ignore[arg-type]
        )

    git_before = replace(
        git_observer.observe(root, target=str(suggestion.file)),
    )
    current = _read_bytes(target)
    precondition = sha256(current)
    identified["git_before"] = git_before
    identified["precondition_passed"] = precondition == suggestion.before_sha256
    identified["before_sha256"] = suggestion.before_sha256
    identified["after_sha256"] = suggestion.after_sha256

    messages: list[str] = []
    if git_before.is_repository and git_before.target_already_modified:
        messages.append("Target file already has uncommitted changes.")

    if not apply:
        return FixApplicationResult(
            status=ApplicationStatus.DRY_RUN,
            reason="dry run: no file was written. Add --apply to write it",
            messages=tuple([*messages, "No files were modified."]),
            **identified,  # type: ignore[arg-type]
        )

    if precondition != suggestion.before_sha256:
        return FixApplicationResult(
            status=ApplicationStatus.STALE,
            reason=(
                f"the target file changed since the suggestion was generated: "
                f"the proposal describes {suggestion.before_sha256} and the file "
                f"on disk is {precondition}. ReproCheck does not merge and does "
                f"not overwrite; run `reprocheck suggest` again"
            ),
            messages=tuple([*messages, "Nothing was written."]),
            **identified,  # type: ignore[arg-type]
        )

    return _apply_now(
        root=root,
        target=target,
        suggestion=suggestion,
        git_before=git_before,
        current=current,
        messages=messages,
        identified=identified,
        state_dir=state_dir,
    )


def run_rollback(
    project: str | Path,
    record_id: str,
    *,
    state_dir: Path | None = None,
) -> FixApplicationResult:
    """Restore the bytes an application replaced, if the file still matches."""
    from reprocheck.fix.records import load_record

    root = resolve_target(project)
    record = load_record(root, record_id, state_dir=state_dir)
    if record is None:
        return FixApplicationResult(
            status=ApplicationStatus.NOT_APPLICABLE,
            reprocheck_version=__version__,
            requested=record_id,
            reason=(
                f"no application record {record_id} was found for this project in "
                f"the state directory"
            ),
            messages=("Nothing was written.",),
        )

    target = root / record.file
    base = dict(
        reprocheck_version=__version__,
        requested=record_id,
        suggestion_id=record.suggestion_id,
        finding_id=record.finding_id,
        safety=record.safety,
        file=record.file,
        before_sha256=record.before_sha256,
        after_sha256=record.after_sha256,
        backup_path=record.backup_path,
        record_id=record.record_id,
        git_before=git_observer.observe(root, target=record.file),
    )
    if not target.is_file():
        return FixApplicationResult(
            status=ApplicationStatus.STALE,
            reason=f"the file {record.file} no longer exists, so nothing was restored",
            rollback=RollbackState.SKIPPED_STALE,
            messages=("Nothing was written.",),
            **base,  # type: ignore[arg-type]
        )

    current = _read_bytes(target)
    if sha256(current) != record.after_sha256:
        return FixApplicationResult(
            status=ApplicationStatus.STALE,
            reason=(
                f"{record.file} changed after the fix, so an automatic rollback "
                f"would overwrite work done since then. ReproCheck does not do "
                f"that: the backup is kept at {record.backup_path}"
            ),
            rollback=RollbackState.SKIPPED_STALE,
            precondition_passed=False,
            messages=("Nothing was written.",),
            **base,  # type: ignore[arg-type]
        )

    backup = Path(record.backup_path)
    if not backup.is_file():
        return FixApplicationResult(
            status=ApplicationStatus.STALE,
            reason=f"the backup {record.backup_path} is missing, so nothing was restored",
            rollback=RollbackState.SKIPPED_STALE,
            messages=("Nothing was written.",),
            **base,  # type: ignore[arg-type]
        )

    original = _read_bytes(backup)
    try:
        _atomic_write(target, original)
    except OSError as exc:
        return FixApplicationResult(
            status=ApplicationStatus.ROLLBACK_FAILED,
            reason=f"the backup could not be restored: {exc}",
            rollback=RollbackState.FAILED,
            precondition_passed=True,
            messages=("The file may differ from both states.",),
            **base,  # type: ignore[arg-type]
        )

    restored = _read_bytes(target)
    if sha256(restored) != record.before_sha256:
        return FixApplicationResult(
            status=ApplicationStatus.ROLLBACK_FAILED,
            reason=(
                "the file was written but its content does not match the backup "
                "that was restored"
            ),
            rollback=RollbackState.FAILED,
            precondition_passed=True,
            messages=("This is a ReproCheck error; the backup is kept.",),
            **base,  # type: ignore[arg-type]
        )

    return FixApplicationResult(
        status=ApplicationStatus.ROLLED_BACK,
        reason=(
            f"the bytes written by {record.record_id} were restored and the "
            f"file now matches {record.before_sha256}"
        ),
        rollback=RollbackState.SUCCEEDED,
        precondition_passed=True,
        applied=False,
        validation=Validation(performed=False),
        git_after=git_observer.observe(root, target=record.file),
        messages=("The file is back to the state it had before the fix.",),
        **base,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _refusal(suggestion, suggestion_id: str) -> str | None:
    if suggestion.safety is not Safety.SAFE:
        return (
            f"{suggestion.suggestion_id} is {suggestion.safety.value}, not SAFE. "
            f"{REJECTED}"
        )
    if suggestion.unified_diff is None or suggestion.after is None:
        return f"{suggestion_id} carries no patch, so there is nothing to write"
    if not suggestion.can_auto_apply_later:
        return (
            f"{suggestion_id} is not marked as applicable: it lacks the byte "
            f"preconditions an application needs"
        )
    return None


def _apply_now(
    *,
    root: Path,
    target: Path,
    suggestion,
    git_before,
    current: bytes,
    messages: list[str],
    identified: dict,
    state_dir: Path | None,
) -> FixApplicationResult:
    after_bytes = str(suggestion.after).encode("utf-8")
    record_id = _record_id(suggestion.suggestion_id)
    backup = _backup_path(root, record_id, state_dir=state_dir)
    try:
        _write_backup(backup, current)
    except OSError as exc:
        return FixApplicationResult(
            status=ApplicationStatus.NOT_APPLICABLE,
            reason=f"the backup could not be created at {backup}: {exc}",
            messages=tuple([*messages, "Nothing was written."]),
            **identified,  # type: ignore[arg-type]
        )

    try:
        _atomic_write(target, after_bytes)
        written = _read_bytes(target)
        if sha256(written) != suggestion.after_sha256:
            raise FixError(
                "the file on disk does not match the proposed content "
                f"(expected {suggestion.after_sha256}, found {sha256(written)})"
            )
        resolved, count, detail = _rescan(root, suggestion.finding_id)
        if not resolved:
            raise FixError(
                f"{suggestion.finding_id} is still reported after the write: {detail}"
            )
    except (OSError, FixError) as exc:
        return _restore(
            root=root,
            target=target,
            original=current,
            expected=suggestion.before_sha256,
            reason=str(exc),
            messages=messages,
            identified=identified,
            record_id=record_id,
            backup=backup,
            state_dir=state_dir,
            suggestion=suggestion,
        )

    git_after = git_observer.observe(root, target=str(suggestion.file))
    unexpected = _unexpected_paths(git_before, git_after, str(suggestion.file))
    result = FixApplicationResult(
        status=ApplicationStatus.APPLIED,
        reason="the proposed content was written and validated",
        applied=True,
        validation=Validation(
            performed=True,
            after_hash_matches=True,
            finding_resolved=True,
            detail=(
                f"the file matches {suggestion.after_sha256} and "
                f"{suggestion.finding_id} is no longer reported"
            ),
        ),
        backup_path=str(backup),
        record_id=record_id,
        rollback=RollbackState.NOT_NEEDED,
        git_after=git_after,
        unexpected_paths=unexpected,
        findings_after=count,
        messages=tuple(
            [
                *messages,
                "The project now contains one intentional change.",
                *(
                    [
                        f"unexpected path(s) changed during the operation: {', '.join(unexpected)}"
                    ]
                    if unexpected
                    else []
                ),
            ]
        ),
        **identified,  # type: ignore[arg-type]
    )
    return _record(result, root, suggestion, record_id, backup, state_dir)


def _restore(
    *,
    root: Path,
    target: Path,
    original: bytes,
    expected: str,
    reason: str,
    messages: list[str],
    identified: dict,
    record_id: str,
    backup: Path,
    state_dir: Path | None,
    suggestion,
) -> FixApplicationResult:
    """Put the original bytes back and say plainly whether it worked."""
    try:
        _atomic_write(target, original)
    except OSError as exc:  # pragma: no cover - depends on the filesystem
        return _finalise_failure(
            root=root,
            suggestion=suggestion,
            identified=identified,
            record_id=record_id,
            backup=backup,
            state_dir=state_dir,
            reason=f"{reason}; the rollback also failed: {exc}",
            rollback=RollbackState.FAILED,
            messages=[
                *messages,
                "This is a severe ReproCheck error: the file may differ from "
                "both states and the backup is kept.",
            ],
        )
    restored = _read_bytes(target)
    if sha256(restored) != expected:
        return _finalise_failure(
            root=root,
            suggestion=suggestion,
            identified=identified,
            record_id=record_id,
            backup=backup,
            state_dir=state_dir,
            reason=(
                f"{reason}; the file was restored but its content does not match "
                f"the original bytes"
            ),
            rollback=RollbackState.FAILED,
            messages=[
                *messages,
                "This is a severe ReproCheck error: the backup is kept.",
            ],
        )
    return _finalise_failure(
        root=root,
        suggestion=suggestion,
        identified=identified,
        record_id=record_id,
        backup=backup,
        state_dir=state_dir,
        reason=reason,
        rollback=RollbackState.SUCCEEDED,
        messages=[
            *messages,
            "The change was reverted: the file is back to its previous content.",
        ],
    )


def _finalise_failure(
    *,
    root: Path,
    suggestion,
    identified: dict,
    record_id: str,
    backup: Path,
    state_dir: Path | None,
    reason: str,
    rollback: RollbackState,
    messages: list[str],
) -> FixApplicationResult:
    status = (
        ApplicationStatus.ROLLBACK_FAILED
        if rollback is RollbackState.FAILED
        else ApplicationStatus.ROLLED_BACK
    )
    result = FixApplicationResult(
        status=status,
        reason=reason,
        applied=False,
        rollback=rollback,
        rollback_reason=reason,
        backup_path=str(backup),
        record_id=record_id,
        validation=Validation(performed=True, detail=reason),
        git_after=git_observer.observe(root, target=str(suggestion.file)),
        messages=tuple(messages),
        **identified,  # type: ignore[arg-type]
    )
    return _record(result, root, suggestion, record_id, backup, state_dir)


def _record(
    result: FixApplicationResult,
    root: Path,
    suggestion,
    record_id: str,
    backup: Path,
    state_dir: Path | None,
) -> FixApplicationResult:
    """Write the audit record and return the result with its path."""
    from reprocheck.fix.records import write_record

    record = ApplicationRecord(
        record_id=record_id,
        project_identity=project_identity(root),
        project_path=str(root),
        suggestion_id=str(suggestion.suggestion_id),
        finding_id=str(suggestion.finding_id),
        safety=str(suggestion.safety.value),
        file=str(suggestion.file),
        applied_at=utc_now(),
        before_sha256=str(suggestion.before_sha256),
        after_sha256=str(suggestion.after_sha256),
        backup_path=str(backup),
        dry_run=False,
        success=result.applied,
        rollback=result.rollback,
        reprocheck_version=__version__,
        status=result.status,
        reason=result.reason,
        rollback_reason=result.rollback_reason,
    )
    path = write_record(record, root, state_dir=state_dir)
    return replace(result, record_path=str(path))


def _rescan(root: Path, finding_id: str) -> tuple[bool, int, str]:
    """Re-scan statically: the finding must be gone after the write."""
    report = build_report(collect_facts(root))
    remaining = [item for item in report.findings if item.id == finding_id]
    if remaining:
        return False, len(report.findings), remaining[0].message
    return True, len(report.findings), "the finding is no longer reported"


def _unexpected_paths(before, after, target: str) -> tuple[str, ...]:
    """Paths that changed during the operation, excluding the target."""
    new = set(after.changed_paths) - set(before.changed_paths)
    return tuple(sorted(path for path in new if path != target))


# --------------------------------------------------------------------------- #
# Bytes on disk
# --------------------------------------------------------------------------- #


def _read_bytes(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read()


def _atomic_write(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` atomically, preserving its mode.

    The temporary file is created in the same directory so ``os.replace`` stays
    on one filesystem, and it is removed if anything fails.
    """
    mode = path.stat().st_mode if path.exists() else None
    handle, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=TEMP_SUFFIX
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            with contextlib.suppress(OSError, ValueError):
                os.fsync(stream.fileno())
        if mode is not None:
            with contextlib.suppress(OSError):
                os.chmod(temp_name, mode & 0o7777)
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise


def _write_backup(backup: Path, data: bytes) -> None:
    """Store the exact bytes outside the project."""
    backup.parent.mkdir(parents=True, exist_ok=True)
    with backup.open("wb") as stream:
        stream.write(data)
        stream.flush()
        with contextlib.suppress(OSError, ValueError):
            os.fsync(stream.fileno())


def _record_id(suggestion_id: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{suggestion_id}"


def _backup_path(root: Path, record_id: str, *, state_dir: Path | None) -> Path:
    return (
        project_output_dir(root, root=state_dir)
        / BACKUP_DIRNAME
        / record_id
        / "before.bin"
    )
