"""Fix outputs: ``reprocheck-fix.json`` and ``reprocheck-fix.md``.

The report states what was requested, what the preconditions were, whether
anything was written, what was validated and what can still be undone. It never
says the project is fixed: one suggestion was applied, and the rest of the
findings are untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

from reprocheck.fix.models import ApplicationStatus, FixApplicationResult
from reprocheck.output import FIX_MARKDOWN_NAME, FIX_NAME

#: What each status means for the reader, in one line.
STATUS_MEANING = {
    ApplicationStatus.DRY_RUN: "Nothing was written. Add --apply to write it.",
    ApplicationStatus.APPLIED: "The proposed content was written and validated.",
    ApplicationStatus.STALE: "The file changed after the proposal; nothing was written.",
    ApplicationStatus.NOT_APPLICABLE: "The suggestion cannot be applied. Nothing was written.",
    ApplicationStatus.FAILED: "The operation failed.",
    ApplicationStatus.ROLLED_BACK: "The change was reverted; the file is as it was.",
    ApplicationStatus.ROLLBACK_FAILED: "The change could not be reverted. This is a ReproCheck error.",
    ApplicationStatus.VERIFICATION_FAILED: (
        "The change was written, the verification did not hold, and it was "
        "reverted automatically. The file is as it was."
    ),
}


def write_fix_json(result: FixApplicationResult, destination: str | Path) -> Path:
    """Write the result as JSON and return the resolved path."""
    path = Path(destination).expanduser()
    if path.is_dir():
        path = path / FIX_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    path.write_text(payload + "\n", encoding="utf-8")
    return path.resolve()


def write_fix_markdown(result: FixApplicationResult, destination: str | Path) -> Path:
    """Write the result as Markdown and return the resolved path."""
    path = Path(destination).expanduser()
    if path.is_dir():
        path = path / FIX_MARKDOWN_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_fix_markdown(result), encoding="utf-8")
    return path.resolve()


def render_fix_markdown(result: FixApplicationResult) -> str:
    """Return the Markdown document of one fix invocation."""
    sections = [
        _header(),
        _summary(result),
        _precondition(result),
        _change(result),
        _validation(result),
        _verification(result),
        _git(result),
        _recovery(result),
        _technical(result),
    ]
    body = "\n\n".join(section for section in sections if section)
    return f"{body}\n"


def _header() -> str:
    return (
        "# ReproCheck Fix\n\n"
        "One suggestion, requested by identifier. `SAFE` describes the change, "
        "not an obligation: applying it is always an explicit decision."
    )


def _summary(result: FixApplicationResult) -> str:
    lines = [
        "## Summary",
        "",
        f"- Requested: {result.requested or '-'}",
        f"- Status: **{result.status.value}**",
        f"- Mode: {'DRY RUN (nothing was written)' if result.dry_run else 'APPLY'}",
        f"- Suggestion: {result.suggestion_id or '-'}",
        f"- Finding: {result.finding_id or '-'}",
        f"- Safety: {result.safety or '-'}",
        f"- File: `{result.file or '-'}`",
        f"- ReproCheck version: {result.reprocheck_version}",
        f"- At: {result.applied_at}",
        "",
        STATUS_MEANING[result.status],
    ]
    if result.reason:
        lines.extend(["", result.reason])
    if result.messages:
        lines.append("")
        lines.extend(f"- {message}" for message in result.messages)
    return "\n".join(lines)


def _precondition(result: FixApplicationResult) -> str:
    if result.before_sha256 is None and result.after_sha256 is None:
        return ""
    lines = [
        "## Precondition",
        "",
        "- The proposal carries the SHA-256 of the exact bytes it was built "
        "from. The file is re-read as bytes immediately before any write.",
        f"- before: `{result.before_sha256}`",
        f"- after:  `{result.after_sha256}`",
        f"- Precondition: {'PASS' if result.precondition_passed else 'FAIL'}",
    ]
    return "\n".join(lines)


def _change(result: FixApplicationResult) -> str:
    if not result.unified_diff:
        return ""
    return "## Applied diff\n\n```diff\n" + result.unified_diff + "\n```"


def _validation(result: FixApplicationResult) -> str:
    validation = result.validation
    if not validation.performed:
        return ""
    lines = [
        "## Validation",
        "",
        f"- Content hash after the write: "
        f"{'MATCH' if validation.after_hash_matches else 'MISMATCH'}",
        f"- {result.finding_id or 'the finding'} re-scanned: "
        f"{'RESOLVED' if validation.finding_resolved else 'STILL REPORTED'}",
        f"- Findings before: {result.findings_before}"
        + (
            f", after: {result.findings_after}"
            if result.findings_after is not None
            else ""
        ),
    ]
    if validation.detail:
        lines.extend(["", validation.detail])
    return "\n".join(lines)


def _verification(result: FixApplicationResult) -> str:
    """The post-fix verification, as a section of its own.

    Present whenever it ran, and present as "not attempted" with a reason when
    it did not. A section that silently disappears reads as a check that passed,
    and "was the verification run?" is the first question a reader of a fix
    report should be able to answer.
    """
    verification = result.verification
    if not verification.attempted:
        reason = verification.not_attempted_reason or "not_requested"
        return "\n".join(
            [
                "## Verification",
                "",
                "- Attempted: no",
                f"- Reason: `{reason}`",
                "",
                "No project was compared before and after, because nothing was "
                "written to compare.",
            ]
            if reason == "dry_run"
            else [
                "## Verification",
                "",
                "- Attempted: no",
                "- Reason: `--verify` was not given",
                "",
                "Without `--verify` the post-fix state is not compared. The "
                "finding is re-scanned either way; `--verify` adds the target "
                "identity, the regression and the integrity checks.",
            ]
        )

    lines = [
        "## Verification",
        "",
        "- Attempted: yes",
        f"- Result: **{'PASS' if verification.success else 'FAIL'}**",
        f"- Target finding: {verification.target_finding_id or '-'}",
        f"- Target resolved: {'yes' if verification.target_after == 0 and verification.target_before else 'no'}",
        f"- Findings before: {verification.findings_before}, "
        f"after: {verification.findings_after}",
        f"- Removed findings: {len(verification.removed)}",
        f"- Added findings: {len(verification.added)}",
        f"- Regressions: {len(verification.regressions)}",
    ]
    if verification.duration_seconds is not None:
        lines.append(f"- Duration: {verification.duration_seconds}s")
    lines.extend(["", "### Checks", ""])
    for check in verification.checks:
        mark = "pass" if check.passed else "FAIL"
        suffix = f" — {check.detail}" if check.detail else ""
        lines.append(f"- `{check.name}`: **{mark}**{suffix}")
    if verification.regressions:
        lines.extend(["", "### Regressions", ""])
        lines.extend(f"- {item}" for item in verification.regressions)
    if verification.removed:
        lines.extend(["", "### Resolved", ""])
        lines.extend(f"- `{item}`" for item in verification.removed)
    if verification.added:
        lines.extend(["", "### New", ""])
        lines.extend(f"- `{item}`" for item in verification.added)
    lines.extend(
        [
            "",
            "The comparison is a static re-scan. It does not run the project's "
            "tests and does not execute project code.",
        ]
    )
    return "\n".join(lines)


def _git(result: FixApplicationResult) -> str:
    before, after = result.git_before, result.git_after
    if not before.is_repository and not after.is_repository:
        return ""
    lines = [
        "## Git",
        "",
        "Observed with read-only commands. ReproCheck never commits, branches, "
        "checks out, resets or cleans.",
        "",
        f"- HEAD before: `{before.head or '-'}`",
        f"- HEAD after:  `{after.head or '-'}`",
        f"- changed paths before: {len(before.changed_paths)}",
        f"- changed paths after:  {len(after.changed_paths)}",
    ]
    if before.target_already_modified:
        lines.append(
            "- The target file already had uncommitted changes before the operation."
        )
    if result.unexpected_paths:
        lines.extend(
            [
                "",
                "**Anomaly:** these paths changed during the operation and were "
                "not touched by ReproCheck: " + ", ".join(result.unexpected_paths),
            ]
        )
    return "\n".join(lines)


def _recovery(result: FixApplicationResult) -> str:
    lines = [
        "## Backup, record and rollback",
        "",
        f"- Rollback state: {result.rollback.value}",
    ]
    if result.rollback_reason:
        lines.append(f"- Rollback reason: {result.rollback_reason}")
    if result.backup_path:
        lines.append(f"- Backup (outside the project): `{result.backup_path}`")
    if result.record_id:
        lines.append(f"- Record id: `{result.record_id}`")
    if result.record_path:
        lines.append(f"- Record: `{result.record_path}`")
        lines.append(
            "- `reprocheck fix <path> --rollback "
            f"{result.record_id}` restores the previous bytes while the file "
            "still matches the hash this record stores."
        )
    return "\n".join(lines)


def _technical(result: FixApplicationResult) -> str:
    from reprocheck.fix.models import FIX_SCHEMA_VERSION

    return "\n".join(
        [
            "## Technical details",
            "",
            f"- Fix schema version: {FIX_SCHEMA_VERSION}",
            "- The write goes through a temporary file in the target's own "
            "directory, `fsync` and `os.replace`, preserving the file mode",
            "- The bytes written are the bytes the rule proposed; no formatter "
            "rebuilds the file",
        ]
    )
