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

from reprocheck.checks.conflicts import Verdict, compare
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
#: A Python pin written in a Conda environment file.
#:
#: It is deliberately **not** KIND_PROJECT. RC103 compares project
#: declarations against each other, and folding Conda in would report the same
#: disagreement twice under two ids. Conda has its own rule, RC230, which knows
#: how to translate Conda's ``=`` pins, so the reconciliation happens once, in
#: the place that understands the syntax.
KIND_CONDA = "conda"

#: Source prefixes that identify a Conda environment file.
CONDA_SOURCE_PREFIXES = ("environment.yml", "environment.yaml", "environment-")

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
    if requirement.source.startswith(CONDA_SOURCE_PREFIXES):
        return KIND_CONDA
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
    if kind == KIND_CONDA:
        # A Conda pin is not PEP 440: `python=3.11` is the 3.11 series. The
        # scanner already produced the value as a Conda spec, so it is
        # translated and the wildcard opened into an interval. Without this a
        # perfectly ordinary environment would be reported as an unparsable
        # declaration by RC104, which is exactly the kind of false positive
        # this kind exists to prevent.
        from reprocheck.scanners.conda import conda_to_pep440

        translated = conda_to_pep440(value)
        if translated is None:
            return value
        from reprocheck.checks.conda import widen_wildcard_pin

        return widen_wildcard_pin(translated)
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

    Incompatibility is only claimed when
    :mod:`reprocheck.checks.conflicts` can prove it, so an undecidable pair
    never becomes an error.
    """
    project = [item for item in declarations if item.kind == KIND_PROJECT]
    entries = [
        (item.value, str(item.specifier))
        for item in project
        if item.specifier is not None
    ]
    entries += [
        (item.value, f"=={item.version}")
        for item in project
        if item.specifier is None and item.version
    ]
    if not entries:
        return []

    for index, (left_label, left_text) in enumerate(entries):
        for right_label, right_text in entries[index + 1 :]:
            result = compare(left_text, right_text)
            if result.verdict is not Verdict.INCOMPATIBLE:
                continue
            return [
                Finding(
                    id=RC103_NO_OVERLAP,
                    title="Declared Python constraints have no overlapping version",
                    severity=Severity.ERROR,
                    category="python",
                    message=(
                        f"'{left_label}' and '{right_label}' cannot both be "
                        f"satisfied: {result.detail}."
                    ),
                    evidence="; ".join(sorted(text for _, text in entries)),
                    confidence=Confidence.HIGH,
                )
            ]
    return []


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
