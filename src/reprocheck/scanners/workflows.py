"""Static extraction of ``uses:`` references from GitHub Actions workflows.

The parser is line-based on purpose: it must not execute, import or resolve
anything, and it must not confuse a commented line for a declaration. No remote
is ever contacted, and the ref is recorded exactly as written.
"""

from __future__ import annotations

import re
from pathlib import Path

from reprocheck.facts import (
    REF_ACTION,
    REF_DOCKER,
    REF_LOCAL_ACTION,
    REF_LOCAL_WORKFLOW,
    REF_REUSABLE_WORKFLOW,
    WorkflowReference,
)
from reprocheck.scanners.files import scan_workflow_files

# Only a full 40-character hexadecimal commit counts as an immutable pin.
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHORT_SHA_RE = re.compile(r"^[0-9a-f]{7,39}$")
_DOCKER_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

_USES_RE = re.compile(r"^(?P<indent>\s*)(?:-[ \t]+)?uses:[ \t]*(?P<value>.+?)[ \t]*$")
_JOBS_RE = re.compile(r"^jobs:[ \t]*(?:#.*)?$")
_JOB_RE = re.compile(r"^(?P<indent>\s+)(?P<name>[^\s:#][^:]*):[ \t]*(?:#.*)?$")
_STEP_NAME_RE = re.compile(r"^[ \t]*-[ \t]+name:[ \t]*(?P<value>.+?)[ \t]*$")


def scan_workflow_references(root: Path) -> list[WorkflowReference]:
    """Return every ``uses:`` reference declared in the project workflows."""
    found: list[WorkflowReference] = []
    for relative in scan_workflow_files(root):
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found.extend(_parse_workflow(relative, text))
    return found


def _parse_workflow(relative: str, text: str) -> list[WorkflowReference]:
    found: list[WorkflowReference] = []
    in_jobs = False
    job: str | None = None
    step: str | None = None

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue

        if _JOBS_RE.match(line):
            in_jobs = True
            job = None
            continue

        if in_jobs:
            job_match = _JOB_RE.match(line)
            if job_match and _is_job_key(job_match):
                job = job_match.group("name").strip()
                step = None
                continue

        step_match = _STEP_NAME_RE.match(line)
        if step_match:
            step = _unquote(step_match.group("value"))

        uses = _USES_RE.match(line)
        if uses:
            value = _unquote(_strip_trailing_comment(uses.group("value")))
            if value:
                found.append(_build_reference(relative, number, value, job, step))
            step = None
    return found


#: Keys that belong to a job or a step, never to a job name.
_JOB_INNER_KEYS = frozenset(
    {
        "container",
        "continue-on-error",
        "defaults",
        "env",
        "if",
        "name",
        "needs",
        "outputs",
        "permissions",
        "runs-on",
        "services",
        "steps",
        "strategy",
        "timeout-minutes",
        "with",
    }
)


def _is_job_key(match: re.Match[str]) -> bool:
    """True when a mapping key looks like a job declaration.

    Job headers sit at low indentation under ``jobs:``; the known inner keys
    are excluded so ``steps:`` is never mistaken for a job.
    """
    name = match.group("name").strip()
    if not name or name in _JOB_INNER_KEYS:
        return False
    return len(match.group("indent")) <= 4


def _strip_trailing_comment(value: str) -> str:
    """Drop a trailing YAML comment, keeping ``#`` inside a value."""
    if "#" not in value:
        return value
    head, _, _ = value.partition("#")
    return head.strip()


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1].strip()
    return text


def _build_reference(
    relative: str,
    number: int,
    value: str,
    job: str | None,
    step: str | None,
) -> WorkflowReference:
    if value.lower().startswith("docker://"):
        return _docker_reference(relative, number, value, job, step)

    if value.startswith("./") or value.startswith("../"):
        target = value.split("@", 1)[0]
        return WorkflowReference(
            file=relative,
            line=number,
            raw=value,
            target=target,
            ref=None,
            reference_type=(
                REF_LOCAL_WORKFLOW if _looks_like_workflow(target) else REF_LOCAL_ACTION
            ),
            job=job,
            step=step,
            is_local=True,
            is_sha=False,
            is_mutable=False,
        )

    target, _, ref = value.rpartition("@")
    if not target:
        # A bare ``owner/repo`` has no ref at all, which is mutable by default.
        target, ref = value, ""
    return WorkflowReference(
        file=relative,
        line=number,
        raw=value,
        target=target,
        ref=ref,
        reference_type=(
            REF_REUSABLE_WORKFLOW if _looks_like_workflow(target) else REF_ACTION
        ),
        job=job,
        step=step,
        is_local=False,
        is_sha=bool(_FULL_SHA_RE.match(ref)),
        is_mutable=not _FULL_SHA_RE.match(ref),
    )


def _looks_like_workflow(target: str) -> bool:
    return "/.github/workflows/" in target and target.endswith((".yml", ".yaml"))


def _docker_reference(
    relative: str,
    number: int,
    value: str,
    job: str | None,
    step: str | None,
) -> WorkflowReference:
    image = value[len("docker://") :]
    if "@" in image:
        target, _, digest = image.partition("@")
        pinned = bool(_DOCKER_DIGEST_RE.match(digest))
        return WorkflowReference(
            file=relative,
            line=number,
            raw=value,
            target=f"docker://{target}",
            ref=digest,
            reference_type=REF_DOCKER,
            job=job,
            step=step,
            is_local=False,
            is_sha=pinned,
            is_mutable=not pinned,
        )
    target, _, tag = image.rpartition(":")
    if not target:
        target, tag = image, ""
    return WorkflowReference(
        file=relative,
        line=number,
        raw=value,
        target=f"docker://{target}",
        ref=tag,
        reference_type=REF_DOCKER,
        job=job,
        step=step,
        is_local=False,
        is_sha=False,
        is_mutable=True,
    )


def is_full_sha(ref: str | None) -> bool:
    """True only for a full 40-character hexadecimal commit SHA."""
    return bool(ref) and bool(_FULL_SHA_RE.match(ref or ""))


def is_short_sha(ref: str | None) -> bool:
    """True for an abbreviated commit SHA, which is not treated as pinned."""
    return bool(ref) and bool(_SHORT_SHA_RE.match(ref or ""))
