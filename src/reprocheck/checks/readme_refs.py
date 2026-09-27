"""Checks for filesystem references documented in the README (RC130-RC132).

Only clear-cut references are verified: a command argument that looks like a
repository-relative path. Commands are never executed.
"""

from __future__ import annotations

from pathlib import Path

from reprocheck.facts import Facts, ReadmeReference
from reprocheck.models import Confidence, Finding, Severity
from reprocheck.scanners.readme import (
    KIND_DIRECTORY,
    KIND_REQUIREMENTS,
    KIND_TEST_PATH,
)

RC130_MISSING_REQUIREMENTS = "RC130"
RC131_MISSING_SCRIPT = "RC131"
RC132_MISSING_DIRECTORY = "RC132"

_MESSAGES = {
    KIND_REQUIREMENTS: (
        RC130_MISSING_REQUIREMENTS,
        "README documents a requirements file that does not exist",
        Severity.WARNING,
        Confidence.HIGH,
    ),
    KIND_TEST_PATH: (
        RC131_MISSING_SCRIPT,
        "README documents a test path that does not exist",
        Severity.WARNING,
        Confidence.HIGH,
    ),
    KIND_DIRECTORY: (
        RC132_MISSING_DIRECTORY,
        "README documents a directory that does not exist",
        Severity.WARNING,
        Confidence.MEDIUM,
    ),
}
_SCRIPT_KINDS = frozenset({"script", KIND_TEST_PATH})


def check_readme_references(facts: Facts) -> list[Finding]:
    """Verify that documented paths exist in the repository."""
    root = Path(facts.project.path)
    if not root.is_dir():
        return []

    findings: list[Finding] = []
    for reference in facts.readme_references:
        identifier, title, severity, confidence = _rule_for(reference)
        if (root / reference.value).exists():
            continue
        findings.append(
            Finding(
                id=identifier,
                title=title,
                severity=severity,
                category="documentation",
                message=(
                    f"{reference.file}:{reference.line} documents "
                    f"'{reference.value}', which was not found in the repository."
                ),
                evidence=reference.command,
                file=reference.file,
                line=reference.line,
                confidence=confidence,
            )
        )
    return findings


def _rule_for(reference: ReadmeReference) -> tuple[str, str, Severity, Confidence]:
    if reference.kind in _SCRIPT_KINDS:
        return (
            RC131_MISSING_SCRIPT,
            "README documents a script that does not exist",
            Severity.WARNING,
            Confidence.HIGH,
        )
    return _MESSAGES[reference.kind]
