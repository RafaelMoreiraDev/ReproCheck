"""Deterministic reconciliation of declared Python versions (RC100-RC104).

The rules implemented here are:

* RC101 - the local ``.python-version`` does not satisfy the project requirement;
* RC102 - a CI job tests a Python version the project does not support;
* RC103 - no candidate version satisfies all declared constraints at once;
* RC104 - a declaration could not be parsed as PEP 440;
* RC100 - declarations exist and none of the rules above fired.

PEP 440 parsing and comparison is delegated to :mod:`packaging`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from reprocheck.facts import Facts
from reprocheck.models import Confidence, Finding, PythonRequirement, Severity

RC100_CONSISTENT = "RC100"
RC101_LOCAL_CONFLICT = "RC101"
RC102_CI_CONFLICT = "RC102"
RC103_NO_OVERLAP = "RC103"
RC104_UNPARSABLE = "RC104"

KIND_LOCAL = "local"
KIND_PROJECT = "project"
KIND_CI = "ci"

_VERSION_TOKEN_RE = re.compile(r"\d+(?:\.\d+)*")
_CARET_RE = re.compile(r"^\^(\d+(?:\.\d+)*)$")
_TILDE_RE = re.compile(r"^~(\d+(?:\.\d+)*)$")


@dataclass(frozen=True, slots=True)
class Declaration:
    """A parsed Python version declaration."""

    requirement: PythonRequirement
    kind: str
    version: Version | None = None
    specifier: SpecifierSet | None = None
    error: str | None = None

    @property
    def source(self) -> str:
        return self.requirement.source

    @property
    def value(self) -> str:
        return self.requirement.value


def check_python_versions(facts: Facts) -> list[Finding]:
    """Apply every Python version reconciliation rule."""
    declarations = [_parse(item) for item in facts.python_requirements]
    unparsable = [item for item in declarations if item.error]
    usable = [item for item in declarations if not item.error]

    project_specifiers = [item for item in usable if item.kind == KIND_PROJECT]
    local_versions = [item for item in usable if item.kind == KIND_LOCAL]
    ci_versions = [item for item in usable if item.kind == KIND_CI]

    findings: list[Finding] = []

    for item in unparsable:
        findings.append(_unparsable_finding(item))

    findings.extend(_local_conflicts(local_versions, project_specifiers))
    findings.extend(_ci_conflicts(ci_versions, project_specifiers))
    findings.extend(_no_overlap(usable))

    if declarations and not findings:
        findings.append(_consistent(len(declarations)))
    return findings


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def _parse(requirement: PythonRequirement) -> Declaration:
    kind = _kind_of(requirement)
    raw = requirement.value.strip()
    normalised = _normalise(raw, kind)

    version = _as_version(normalised)
    if version is not None:
        return Declaration(requirement, kind, version=version)

    specifier = _as_specifier(normalised)
    if specifier is not None and _has_comparison(specifier):
        return Declaration(requirement, kind, specifier=specifier)

    return Declaration(
        requirement,
        kind,
        error=f"'{requirement.value}' is not a PEP 440 version or specifier",
    )


def _kind_of(requirement: PythonRequirement) -> str:
    if requirement.source == ".python-version":
        return KIND_LOCAL
    if requirement.source.startswith("pyproject.toml"):
        return KIND_PROJECT
    return KIND_CI


def _normalise(value: str, kind: str) -> str:
    """Translate tool-specific syntax into PEP 440 where it is unambiguous."""
    if kind == KIND_PROJECT:
        caret = _CARET_RE.match(value)
        if caret:
            return _caret_to_specifier(caret.group(1))
        tilde = _TILDE_RE.match(value)
        if tilde:
            return _tilde_to_specifier(tilde.group(1))
    return value


def _caret_to_specifier(version: str) -> str:
    parts = [int(part) for part in version.split(".")]
    upper = _next_major(parts)
    return f">={version},<{upper}"


def _tilde_to_specifier(version: str) -> str:
    return f">={version},<{_next_major([int(p) for p in version.split('.')])}"


def _next_major(parts: list[int]) -> str:
    return f"{parts[0] + 1}.0.0"


def _as_version(value: str) -> Version | None:
    try:
        return Version(value)
    except InvalidVersion:
        return None


def _as_specifier(value: str) -> SpecifierSet | None:
    try:
        return SpecifierSet(value)
    except InvalidSpecifier:
        return None


def _has_comparison(specifier: SpecifierSet) -> bool:
    return any(
        item.operator in {">=", ">", "<=", "<", "==", "~=", "!="} for item in specifier
    )


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


def _unparsable_finding(declaration: Declaration) -> Finding:
    return Finding(
        id=RC104_UNPARSABLE,
        title="Python version declaration could not be parsed",
        severity=Severity.INFO,
        category="python",
        message=(
            f"{declaration.source} declares '{declaration.value}', which is not "
            "a PEP 440 version or version specifier; it was not reconciled."
        ),
        evidence=declaration.error,
        file=declaration.requirement.file,
        line=declaration.requirement.line,
        confidence=Confidence.HIGH,
    )


def _local_conflicts(
    local: list[Declaration], project: list[Declaration]
) -> list[Finding]:
    constraints = [item for item in project if _is_constraint(item)]
    if not constraints:
        return []
    findings: list[Finding] = []
    for item in local:
        if item.version is None:
            continue
        blocking = [c for c in constraints if not _satisfies(c, item.version)]
        if not blocking:
            continue
        for constraint in blocking:
            findings.append(
                Finding(
                    id=RC101_LOCAL_CONFLICT,
                    title="Local Python version conflicts with project requirement",
                    severity=Severity.WARNING,
                    category="python",
                    message=(
                        f".python-version selects Python {item.value}, which does "
                        f"not satisfy '{constraint.value}' declared by "
                        f"{constraint.source}."
                    ),
                    evidence=f"{item.value} vs {constraint.value}",
                    file=item.requirement.file,
                    line=item.requirement.line,
                    confidence=Confidence.HIGH,
                )
            )
    return findings


def _ci_conflicts(ci: list[Declaration], project: list[Declaration]) -> list[Finding]:
    constraints = [item for item in project if _is_constraint(item)]
    if not constraints:
        return []
    findings: list[Finding] = []
    for item in ci:
        if item.version is None:
            continue
        for constraint in constraints:
            if _satisfies(constraint, item.version):
                continue
            findings.append(
                Finding(
                    id=RC102_CI_CONFLICT,
                    title="CI tests a Python version the project does not support",
                    severity=Severity.WARNING,
                    category="python",
                    message=(
                        f"{item.source} runs Python {item.value}, which does not "
                        f"satisfy '{constraint.value}' declared by "
                        f"{constraint.source}."
                    ),
                    evidence=f"{item.value} vs {constraint.value}",
                    file=item.requirement.file,
                    line=item.requirement.line,
                    confidence=Confidence.HIGH,
                )
            )
    return findings


def _no_overlap(declarations: list[Declaration]) -> list[Finding]:
    """Report constraints that no version can satisfy at the same time.

    Only *project* declarations take part: a conflict between two of them is
    provable, while a conflict with a local or CI pin is already reported by
    RC101/RC102 (and a CI matrix is expected to list several versions).
    """
    project = [item for item in declarations if item.kind == KIND_PROJECT]
    constraints = [item.specifier for item in project if item.specifier is not None]
    pins = [item for item in project if item.specifier is None and item.version]
    if not constraints and len(pins) < 2:
        return []

    candidates = _candidates(declarations)
    if not candidates:
        return []

    for candidate in candidates:
        if any(candidate not in specifier for specifier in constraints):
            continue
        if pins and not any(candidate == pin.version for pin in pins):
            continue
        return []

    described = [str(specifier) for specifier in constraints] + [
        f"=={pin.version}" for pin in pins
    ]
    return [
        Finding(
            id=RC103_NO_OVERLAP,
            title="Declared Python constraints have no overlapping version",
            severity=Severity.ERROR,
            category="python",
            message=(
                "None of the "
                f"{len(candidates)} candidate version(s) derived from the "
                "declarations satisfies every declared constraint: "
                + "; ".join(sorted(described))
                + "."
            ),
            evidence=(
                "candidates tested: "
                + ", ".join(str(candidate) for candidate in candidates)
            ),
            confidence=Confidence.HIGH,
        )
    ]


def _consistent(count: int) -> Finding:
    return Finding(
        id=RC100_CONSISTENT,
        title="Python version declarations are consistent",
        severity=Severity.INFO,
        category="python",
        message=(
            f"{count} Python version declaration(s) were found and no "
            "inconsistency could be proven."
        ),
        confidence=Confidence.HIGH,
    )


def _is_constraint(declaration: Declaration) -> bool:
    """A project declaration restricts the version: a specifier or an exact pin."""
    return declaration.specifier is not None or declaration.version is not None


def _satisfies(constraint: Declaration, version: Version) -> bool:
    if constraint.specifier is not None:
        return version in constraint.specifier
    if constraint.version is not None:
        return version == constraint.version
    return True


# --------------------------------------------------------------------------- #
# Candidate versions
# --------------------------------------------------------------------------- #


def _candidates(declarations: list[Declaration]) -> list[Version]:
    """Build the set of versions used to test constraint compatibility.

    Candidates come from every concrete version mentioned by the project plus
    the boundaries implied by the declared specifiers.
    """
    raw: set[str] = set()
    for item in declarations:
        raw.update(_VERSION_TOKEN_RE.findall(item.value))
        if item.specifier is not None:
            raw.update(_boundaries(item.specifier))
    versions: set[Version] = set()
    for text in raw:
        for candidate in (text, f"{text}.0"):
            version = _as_version(candidate)
            if version is not None and not version.is_prerelease:
                versions.add(version)
    return sorted(versions)


def _boundaries(specifier: SpecifierSet) -> set[str]:
    found: set[str] = set()
    for item in specifier:
        if not item.version:
            continue
        found.add(item.version)
        if item.operator in {"<", "<="}:
            found.add(_previous_minor(item.version))
    return found


def _previous_minor(version: str) -> str:
    parts = version.split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        numbers = [int(part) for part in parts[:3]]
    except ValueError:  # pragma: no cover - defensive
        return version
    if numbers[1] > 0:
        numbers[1] -= 1
    elif numbers[0] > 0:
        numbers[0] -= 1
    numbers[2] = 0
    return ".".join(str(number) for number in numbers)
