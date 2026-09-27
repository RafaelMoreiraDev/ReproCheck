"""Deterministic audit of GitHub Actions references (RC220-RC223).

ReproCheck knows three things about a remote reference and nothing else:

* whether the ref is a full commit SHA or not;
* whether the same target is referenced with different refs;
* whether a local target exists in the repository.

It never states that a version is obsolete or unsafe: that would require
external, temporal knowledge.
"""

from __future__ import annotations

from pathlib import Path

from reprocheck.facts import (
    REF_DOCKER,
    REF_LOCAL_ACTION,
    REF_LOCAL_WORKFLOW,
    Facts,
    WorkflowReference,
)
from reprocheck.models import Confidence, Finding, Severity
from reprocheck.scanners.workflows import is_short_sha

RC220_MUTABLE_REFERENCE = "RC220"
RC221_INCONSISTENT_REFS = "RC221"
RC222_MISSING_LOCAL_REFERENCE = "RC222"
RC223_MUTABLE_DOCKER_REFERENCE = "RC223"

CATEGORY = "ci-references"

_LOCAL_ACTION_FILES = ("action.yml", "action.yaml")


def check_workflow_references(facts: Facts) -> list[Finding]:
    """Apply every GitHub Actions reference rule."""
    root = Path(facts.project.path) if facts.project.path else None
    return [
        *_mutable_references(facts.workflow_references),
        *_mutable_docker_references(facts.workflow_references),
        *_inconsistent_refs(facts.workflow_references),
        *_missing_local_references(facts.workflow_references, root),
    ]


def _origin(reference: WorkflowReference) -> str:
    location = f"{reference.file}:{reference.line}"
    if reference.job:
        return f"{location} (job '{reference.job}')"
    return location


def _mutable_references(
    references: list[WorkflowReference],
) -> list[Finding]:
    """One finding per distinct external target and mutable ref."""
    grouped: dict[tuple[str, str], list[str]] = {}
    for reference in references:
        if reference.is_local or reference.is_sha:
            continue
        if reference.reference_type == REF_DOCKER:
            continue
        key = (reference.target, reference.ref or "")
        grouped.setdefault(key, []).append(_origin(reference))

    findings: list[Finding] = []
    for (target, ref), locations in sorted(grouped.items()):
        if not ref:
            detail = "without any ref"
        elif is_short_sha(ref):
            detail = f"by the abbreviated SHA '{ref}', not a full commit SHA"
        else:
            detail = f"by the mutable ref '{ref}'"
        findings.append(
            Finding(
                id=RC220_MUTABLE_REFERENCE,
                title="External action referenced by a mutable ref",
                severity=Severity.WARNING,
                category=CATEGORY,
                message=(
                    f"External GitHub Action/workflow '{target}' is referenced "
                    f"{detail} rather than a full commit SHA."
                ),
                evidence="; ".join(sorted(locations)),
                confidence=Confidence.HIGH,
            )
        )
    return findings


def _mutable_docker_references(
    references: list[WorkflowReference],
) -> list[Finding]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for reference in references:
        if reference.reference_type != REF_DOCKER or reference.is_sha:
            continue
        key = (reference.target, reference.ref or "")
        grouped.setdefault(key, []).append(_origin(reference))

    return [
        Finding(
            id=RC223_MUTABLE_DOCKER_REFERENCE,
            title="Docker action referenced by a mutable tag",
            severity=Severity.WARNING,
            category=CATEGORY,
            message=(
                f"Docker image '{target}' is referenced by "
                f"{'tag ' + repr(ref) if ref else 'no tag'} rather than a digest."
            ),
            evidence="; ".join(sorted(locations)),
            confidence=Confidence.HIGH,
        )
        for (target, ref), locations in sorted(grouped.items())
    ]


def _inconsistent_refs(references: list[WorkflowReference]) -> list[Finding]:
    """Report a target referenced with more than one distinct ref."""
    by_target: dict[str, dict[str, list[str]]] = {}
    for reference in references:
        if reference.is_local or reference.reference_type == REF_DOCKER:
            continue
        ref = reference.ref or ""
        by_target.setdefault(reference.target, {}).setdefault(ref, []).append(
            _origin(reference)
        )

    findings: list[Finding] = []
    for target, refs in sorted(by_target.items()):
        if len(refs) < 2:
            continue
        findings.append(
            Finding(
                id=RC221_INCONSISTENT_REFS,
                title="Same action referenced with different refs",
                severity=Severity.WARNING,
                category=CATEGORY,
                message=(
                    f"'{target}' is referenced with {len(refs)} different refs: "
                    + ", ".join(sorted(refs))
                    + ". ReproCheck does not decide which one is intended."
                ),
                evidence="; ".join(
                    f"{ref or 'no ref'} -> {', '.join(sorted(locations))}"
                    for ref, locations in sorted(refs.items())
                ),
                confidence=Confidence.HIGH,
            )
        )
    return findings


def _missing_local_references(
    references: list[WorkflowReference], root: Path | None
) -> list[Finding]:
    if root is None or not root.is_dir():
        return []

    findings: list[Finding] = []
    for reference in references:
        if not reference.is_local:
            continue
        if _local_target_exists(root, reference):
            continue
        expected = (
            "one of action.yml, action.yaml"
            if reference.reference_type == REF_LOCAL_ACTION
            else "the workflow file"
        )
        findings.append(
            Finding(
                id=RC222_MISSING_LOCAL_REFERENCE,
                title="Local action or workflow does not exist",
                severity=Severity.WARNING,
                category=CATEGORY,
                message=(
                    f"{_origin(reference)} references the local path "
                    f"'{reference.target}', which does not exist in the "
                    f"repository; expected {expected}."
                ),
                evidence=f"{reference.file}:{reference.line} -> {reference.target}",
                file=reference.file,
                line=reference.line,
                confidence=Confidence.HIGH,
            )
        )
    return findings


def _local_target_exists(root: Path, reference: WorkflowReference) -> bool:
    relative = _relative_path(reference.target)
    if relative is None:
        return False
    candidate = (root / relative).resolve()
    try:
        if not candidate.is_relative_to(root.resolve()):
            return False
    except (OSError, ValueError):  # pragma: no cover - defensive
        return False

    if reference.reference_type == REF_LOCAL_WORKFLOW:
        return candidate.is_file()
    if candidate.is_dir():
        return any((candidate / name).is_file() for name in _LOCAL_ACTION_FILES)
    return False


def _relative_path(target: str) -> Path | None:
    """Resolve a ``uses:`` local path against the project, or ``None``.

    Only paths inside the project are considered; ``../`` and absolute paths
    are rejected instead of being followed.
    """
    if target.startswith("./"):
        return Path(target[2:])
    if target.startswith(("/", "\\")) or target.startswith("../"):
        return None
    if len(target) > 1 and target[1] == ":":
        return None
    return Path(target)
