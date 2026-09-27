"""Checks for hard-coded and unresolved local paths (RC120-RC121)."""

from __future__ import annotations

from reprocheck.facts import Facts
from reprocheck.models import Confidence, Finding, Severity

RC120_ABSOLUTE_PATH = "RC120"
RC121_MISSING_LOCAL_PATH = "RC121"


def check_absolute_paths(facts: Facts) -> list[Finding]:
    """Report hard-coded absolute local paths found in code and configuration."""
    grouped: dict[str, list[tuple[str, int]]] = {}
    for reference in facts.absolute_paths:
        grouped.setdefault(reference.value, []).append((reference.file, reference.line))

    findings: list[Finding] = []
    for value in sorted(grouped):
        locations = sorted(set(grouped[value]))
        first_file, first_line = locations[0]
        findings.append(
            Finding(
                id=RC120_ABSOLUTE_PATH,
                title="Hard-coded absolute local path detected",
                severity=Severity.WARNING,
                category="paths",
                message=(
                    f"'{value}' is an absolute local path; it only resolves on the "
                    "machine where it was written."
                ),
                evidence="; ".join(f"{file}:{line}" for file, line in locations),
                file=first_file,
                line=first_line,
                confidence=Confidence.HIGH,
            )
        )
    return findings


def check_missing_local_paths(facts: Facts) -> list[Finding]:
    """Report literal paths that do not exist in the scanned project.

    The path may legitimately be produced at runtime or downloaded, so the
    finding is a warning with a careful message, never an error.
    """
    grouped: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for reference in facts.file_references:
        if reference.exists:
            continue
        grouped.setdefault((reference.call, reference.value), []).append(
            (reference.file, reference.line)
        )

    findings: list[Finding] = []
    for call, value in sorted(grouped):
        locations = sorted(set(grouped[(call, value)]))
        first_file, first_line = locations[0]
        findings.append(
            Finding(
                id=RC121_MISSING_LOCAL_PATH,
                title="Referenced local path was not found in the repository",
                severity=Severity.WARNING,
                category="paths",
                message=(
                    f"{call}('{value}') refers to a path that does not exist in "
                    "the repository; it may be created or downloaded at runtime."
                ),
                evidence="; ".join(f"{file}:{line}" for file, line in locations),
                file=first_file,
                line=first_line,
                confidence=Confidence.MEDIUM,
            )
        )
    return findings
