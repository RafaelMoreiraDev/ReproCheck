"""The comparison engine.

A pure function of two report documents. It reads nothing from the filesystem,
contacts nothing, and never re-runs anything: two reports produced by ordinary
``scan`` or ``reproduce`` runs are all the input it needs.

The engine reports *what changed* and stops there. It does not decide whether a
change is an improvement, and it does not suggest what to do about it.
"""

from __future__ import annotations

from collections.abc import Mapping

from reprocheck.diff.models import (
    ChangeKind,
    FactChange,
    FindingChange,
    FindingChanges,
    ReportIdentity,
    ReproducibilityDiff,
    VerdictChange,
)
from reprocheck.diff.normalise import (
    DISPLAY_FIELD,
    LABEL_FIELD,
    changed_attributes,
    normalise_ci,
    normalise_conda,
    normalise_dependencies,
    normalise_findings,
    normalise_python,
    normalise_reproduction,
)

_MISSING_VERDICT_NOTE = (
    "the baseline report predates the verdict model, so no previous verdict exists"
)

#: Verdict values, ordered by how much was verified. Used only to describe the
#: movement, never to call it a regression.
_VERDICT_ORDER = ("NOT_ATTEMPTED", "PARTIAL", "PASS", "FAIL")


def compare_reports(baseline: dict, current: dict) -> ReproducibilityDiff:
    """Return every material difference between two report documents."""
    return ReproducibilityDiff(
        baseline=ReportIdentity.from_report(baseline),
        current=ReportIdentity.from_report(current),
        verdict=_verdict_change(baseline, current),
        findings=_findings(baseline, current),
        dependencies=_facts(
            normalise_dependencies(baseline),
            normalise_dependencies(current),
            "dependency",
        ),
        python=_facts(normalise_python(baseline), normalise_python(current), "python"),
        conda=_facts(normalise_conda(baseline), normalise_conda(current), "conda"),
        ci=_facts(normalise_ci(baseline), normalise_ci(current), "ci"),
        reproduction=_reproduction(baseline, current),
    )


# --------------------------------------------------------------------------- #
# Verdict
# --------------------------------------------------------------------------- #


def _verdict(baseline: dict, current: dict) -> str | None:
    status = (baseline.get("verdict") or {}).get("status")
    return str(status) if status else None


def _verdict_change(baseline: dict, current: dict) -> VerdictChange:
    previous = _verdict(baseline, current)
    current_status = (current.get("verdict") or {}).get("status")
    current_status = str(current_status) if current_status else None
    if previous == current_status:
        return VerdictChange(previous, current_status, changed=False)
    note = _MISSING_VERDICT_NOTE if previous is None else None
    return VerdictChange(previous, current_status, changed=True, note=note)


def movement(previous: str | None, current: str | None) -> str:
    """Describe a verdict movement factually, in words, without judgement.

    The order in :data:`_VERDICT_ORDER` is only a reading aid: ``FAIL`` sits
    last because it is the only proven failure, not because it is "the worst".
    """
    if previous is None:
        return f"no previous verdict -> {current}"
    if current is None:
        return f"{previous} -> no current verdict"
    return f"{previous} -> {current}"


def verdict_positions(previous: str | None, current: str | None) -> tuple[int, int]:
    """Return the reading-aid positions of two verdicts, or ``(-1, -1)``."""
    return (
        _VERDICT_ORDER.index(previous) if previous in _VERDICT_ORDER else -1,
        _VERDICT_ORDER.index(current) if current in _VERDICT_ORDER else -1,
    )


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #


def _findings(baseline: dict, current: dict) -> FindingChanges:
    before = normalise_findings(baseline)
    after = normalise_findings(current)
    added: list[FindingChange] = []
    resolved: list[FindingChange] = []
    changed: list[FindingChange] = []

    for identity in sorted(set(before) | set(after)):
        old = before.get(identity)
        new = after.get(identity)
        if old is None and new is not None:
            added.append(
                FindingChange(ChangeKind.ADDED, identity, current=_finding_payload(new))
            )
        elif old is not None and new is None:
            resolved.append(
                FindingChange(
                    ChangeKind.RESOLVED, identity, previous=_finding_payload(old)
                )
            )
        elif old is not None and new is not None:
            fields = changed_attributes(old, new)
            if fields:
                changed.append(
                    FindingChange(
                        ChangeKind.CHANGED,
                        identity,
                        previous=_finding_payload(old),
                        current=_finding_payload(new),
                        changed_fields=fields,
                    )
                )
    return FindingChanges(
        added=tuple(added), resolved=tuple(resolved), changed=tuple(changed)
    )


def _finding_payload(summary: Mapping[str, str]) -> dict[str, str]:
    """The finding as a reader sees it: no derived presentation fields."""
    return {
        key: value
        for key, value in summary.items()
        if key not in {LABEL_FIELD, DISPLAY_FIELD}
    }


# --------------------------------------------------------------------------- #
# Fact domains
# --------------------------------------------------------------------------- #


def _facts(
    before: Mapping[str, dict[str, str]],
    after: Mapping[str, dict[str, str]],
    domain: str,
) -> tuple[FactChange, ...]:
    changes: list[FactChange] = []
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        if old is None and new is not None:
            changes.append(
                FactChange(
                    domain=domain,
                    key=key,
                    label=new.get(LABEL_FIELD, key),
                    kind=ChangeKind.ADDED,
                    current=_render(new),
                )
            )
        elif old is not None and new is None:
            changes.append(
                FactChange(
                    domain=domain,
                    key=key,
                    label=old.get(LABEL_FIELD, key),
                    kind=ChangeKind.RESOLVED,
                    previous=_render(old),
                )
            )
        elif old is not None and new is not None:
            fields = changed_attributes(old, new)
            if fields:
                changes.append(
                    FactChange(
                        domain=domain,
                        key=key,
                        label=new.get(LABEL_FIELD, key),
                        kind=ChangeKind.CHANGED,
                        previous=_render(old, fields=fields),
                        current=_render(new, fields=fields),
                        changed_fields=fields,
                    )
                )
    return tuple(changes)


def _render(entry: Mapping[str, str], fields: tuple[str, ...] = ()) -> str:
    """Render one normalised entry as a short human string.

    With ``fields`` (a real edit) only those attributes are shown, so the line
    says what changed. Without them the entry's own ``display`` is used, so an
    added dependency reads as a dependency and not as a list of every attribute.
    """
    if fields:
        return ", ".join(f"{name}={entry.get(name, '')}" for name in fields)
    return entry.get(DISPLAY_FIELD) or ", ".join(
        f"{name}={value}"
        for name, value in sorted(entry.items())
        if name not in {LABEL_FIELD, DISPLAY_FIELD}
    )


# --------------------------------------------------------------------------- #
# Reproduction
# --------------------------------------------------------------------------- #


def _reproduction(baseline: dict, current: dict) -> tuple[FactChange, ...]:
    before = normalise_reproduction(baseline)
    after = normalise_reproduction(current)
    if bool(before) == bool(after):
        return _facts(before, after, "reproduction")

    if after and not before:
        return (
            FactChange(
                domain="reproduction",
                key="attempted",
                label="a reproduction attempt",
                kind=ChangeKind.ADDED,
                current="one run was recorded in the current report",
            ),
        )
    return (
        FactChange(
            domain="reproduction",
            key="attempted",
            label="a reproduction attempt",
            kind=ChangeKind.RESOLVED,
            previous="one run was recorded in the baseline report",
        ),
    )
