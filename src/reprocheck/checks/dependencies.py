"""Deterministic audit of declared dependencies (RC200-RC210).

The audit answers one question: *are the declarations of this project mutually
consistent?* Nothing is resolved, installed or downloaded, and no conclusion is
drawn when the declarations cannot be compared.

Coexistence model
-----------------

Two declarations are only required to be satisfiable together when they belong
to the same *scope*:

===============  ==========================================================
Scope            Contents
===============  ==========================================================
``runtime``      runtime declarations
``runtime+dev``  runtime plus dev/test groups: installing the dev group
                 normally installs the project itself
``optional:<x>`` one optional extra; opt-in, so it is never compared with
                 runtime
``build``        ``[build-system] requires``: installed in an isolated build
                 environment
``constraint``   ``-c`` constraint files, compared with the runtime scope
===============  ==========================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from reprocheck.facts import (
    KIND_BUILD,
    KIND_CONSTRAINT,
    KIND_DEV,
    KIND_OPTIONAL,
    KIND_RUNTIME,
    KIND_TEST,
    REF_LOCAL_PATH,
    REF_URL,
    REF_VCS,
    DependencyDeclaration,
    Facts,
    RequirementsInclude,
)
from reprocheck.models import Confidence, Finding, Severity

RC200_CONFLICTING_PINS = "RC200"
RC201_DISJOINT_CONSTRAINTS = "RC201"
RC202_DUPLICATE_DECLARATION = "RC202"
RC203_UNBOUNDED_RUNTIME = "RC203"
RC204_MIXED_PINNING = "RC204"
RC205_RUNTIME_DEV_DIVERGENCE = "RC205"
RC206_MISSING_INCLUDE = "RC206"
RC207_MISSING_CONSTRAINT_FILE = "RC207"
RC208_EXTERNAL_REFERENCE = "RC208"
RC209_MUTABLE_VCS_REFERENCE = "RC209"
RC210_LOCAL_PATH_DEPENDENCY = "RC210"

CATEGORY = "dependencies"

_VERSION_TOKEN_RE = re.compile(r"\d+(?:\.\d+)*")
_ABSOLUTE_PATH_RE = re.compile(r"^(?:file://)?/?[A-Za-z]:[\\/]|^file:///")


class Relation(StrEnum):
    """How two declarations of the same package relate to each other."""

    IDENTICAL = "identical"
    DIVERGENT = "divergent"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def check_dependency_declarations(facts: Facts) -> list[Finding]:
    """:func:`check_dependencies` wired to the scanned project facts."""
    root = _project_root(facts)
    return check_dependencies(
        facts.dependency_declarations,
        facts.requirement_includes,
        root,
    )


def _project_root(facts: Facts) -> Path | None:
    if not facts.project.path:
        return None
    candidate = Path(facts.project.path)
    return candidate if candidate.is_dir() else None


def check_dependencies(
    declarations: list[DependencyDeclaration],
    includes: list[RequirementsInclude],
    root: Path | None = None,
) -> list[Finding]:
    """Apply every dependency consistency rule."""
    findings: list[Finding] = []
    findings.extend(_conflicting_declarations(declarations))
    findings.extend(_unbounded_runtime(declarations))
    findings.extend(_mixed_pinning(declarations))
    findings.extend(_missing_includes(includes))
    findings.extend(_external_references(declarations))
    findings.extend(_local_path_dependencies(declarations, root))
    return findings


# --------------------------------------------------------------------------- #
# Conflict engine
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Pair:
    """Two declarations of the same package that may need to coexist."""

    left: DependencyDeclaration
    right: DependencyDeclaration
    relation: Relation
    detail: str

    @property
    def scope(self) -> str:
        return _scope_of(self.left, self.right)

    @property
    def crosses_optional(self) -> bool:
        kinds = {self.left.kind, self.right.kind}
        return KIND_OPTIONAL in kinds


def _scope_of(left: DependencyDeclaration, right: DependencyDeclaration) -> str:
    kinds = {left.kind, right.kind}
    if kinds == {KIND_BUILD}:
        return "build"
    if kinds == {KIND_CONSTRAINT}:
        return "constraint"
    if kinds == {KIND_CONSTRAINT, KIND_RUNTIME} or kinds == {
        KIND_CONSTRAINT,
        KIND_DEV,
    }:
        return "constraint"
    if kinds == {KIND_OPTIONAL}:
        return f"optional:{left.group}"
    if kinds <= {KIND_RUNTIME, KIND_DEV, KIND_TEST}:
        return "runtime+dev"
    if kinds == {KIND_OPTIONAL, KIND_RUNTIME} or kinds == {
        KIND_OPTIONAL,
        KIND_DEV,
    }:
        return "optional-mixed"
    return "other"


def _conflicting_declarations(
    declarations: list[DependencyDeclaration],
) -> list[Finding]:
    findings: list[Finding] = []
    for name in sorted({item.name for item in declarations if item.name}):
        group = [item for item in declarations if item.name == name]
        if len(group) < 2:
            continue
        for pair in combinations(_deduplicate(group), 2):
            relation, detail = _classify(pair[0], pair[1])
            finding = _finding_for_pair(Pair(pair[0], pair[1], relation, detail))
            if finding is not None:
                findings.append(finding)
    return findings


def _deduplicate(
    group: list[DependencyDeclaration],
) -> list[DependencyDeclaration]:
    """Keep one declaration per distinct (source, group, specifier, marker)."""
    seen: set[tuple[object, ...]] = set()
    unique: list[DependencyDeclaration] = []
    for item in group:
        key = (item.source, item.group, item.specifier, item.marker, item.reference)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _classify(
    left: DependencyDeclaration, right: DependencyDeclaration
) -> tuple[Relation, str]:
    """Compare two declarations of the same package."""
    if left.is_external or right.is_external:
        return (Relation.UNKNOWN, "external reference")
    if left.is_conditional or right.is_conditional:
        if left.marker != right.marker:
            return (Relation.UNKNOWN, "different environment markers")
    if left.extras != right.extras:
        return (Relation.DIVERGENT, "different extras")

    left_spec, left_error = _parse_specifier(left)
    right_spec, right_error = _parse_specifier(right)
    if left_error or right_error:
        return (Relation.UNKNOWN, f"unparsable specifier: {left_error or right_error}")

    if _specifier_text(left_spec) == _specifier_text(right_spec):
        if not left.is_bounded and not right.is_bounded:
            return (Relation.IDENTICAL, "both declarations are unbounded")
        return (Relation.IDENTICAL, "identical version constraint")

    if not left.is_bounded or not right.is_bounded:
        return (
            Relation.DIVERGENT,
            f"'{left.specifier or 'no constraint'}' vs "
            f"'{right.specifier or 'no constraint'}'",
        )

    candidates = _candidates(left_spec, right_spec)
    if not _has_common_version(candidates, left_spec, right_spec):
        if _has_exclusion(left_spec) or _has_exclusion(right_spec):
            # ``!=`` rules can hide versions no candidate happens to cover.
            return (
                Relation.DIVERGENT,
                f"no common version among {len(candidates)} candidates, but "
                "exclusions are involved",
            )
        return (
            Relation.INCOMPATIBLE,
            f"no version satisfies both ({len(candidates)} candidates tested)",
        )
    return (
        Relation.DIVERGENT,
        f"compatible but declared as '{left.specifier}' and '{right.specifier}'",
    )


def _finding_for_pair(pair: Pair) -> Finding | None:
    if pair.scope in {"build", "other"}:
        # Build requirements are installed in an isolated environment, so they
        # are never required to satisfy the runtime constraints.
        return None

    name = pair.left.name
    where = (
        f"{_origin(pair.left)} declares '{pair.left.raw or name}' and "
        f"{_origin(pair.right)} declares '{pair.right.raw or name}'"
    )
    evidence = f"{_origin(pair.left)} | {_origin(pair.right)} | {pair.detail}"

    if pair.relation is Relation.INCOMPATIBLE and pair.crosses_optional:
        return Finding(
            id=RC201_DISJOINT_CONSTRAINTS,
            title="Optional extra conflicts with the runtime constraint",
            severity=Severity.WARNING,
            category=CATEGORY,
            message=(
                f"'{name}' has no version satisfying both the runtime and the "
                f"extra declaration ({_scope_label(pair)}); the extra is opt-in, "
                f"so this may be intentional: {where}."
            ),
            evidence=evidence,
            confidence=Confidence.MEDIUM,
        )

    if (
        pair.relation is Relation.INCOMPATIBLE
        and pair.left.is_pinned
        and pair.right.is_pinned
    ):
        return Finding(
            id=RC200_CONFLICTING_PINS,
            title="Conflicting exact pins for the same dependency",
            severity=Severity.WARNING,
            category=CATEGORY,
            message=(
                f"'{name}' is pinned to different exact versions: "
                f"{_pin_of(pair.left)} and {_pin_of(pair.right)}."
            ),
            evidence=evidence,
            confidence=Confidence.HIGH,
        )

    if pair.relation is Relation.INCOMPATIBLE:
        severity = Severity.WARNING if pair.crosses_optional else Severity.ERROR
        return Finding(
            id=RC201_DISJOINT_CONSTRAINTS,
            title="Dependency constraints cannot be satisfied together",
            severity=severity,
            category=CATEGORY,
            message=(
                f"'{name}' has no version satisfying both declarations "
                f"({_scope_label(pair)}): {where}."
            ),
            evidence=evidence,
            confidence=Confidence.MEDIUM if pair.crosses_optional else Confidence.HIGH,
        )

    if pair.relation is Relation.IDENTICAL:
        return Finding(
            id=RC202_DUPLICATE_DECLARATION,
            title="Dependency declared more than once",
            severity=Severity.INFO,
            category=CATEGORY,
            message=(
                f"'{name}' is declared twice with the same constraint "
                f"({pair.detail}): {_origin(pair.left)} and {_origin(pair.right)}."
            ),
            evidence=evidence,
            confidence=Confidence.HIGH,
        )

    if pair.relation is Relation.DIVERGENT and pair.crosses_optional:
        return None

    if pair.relation is Relation.DIVERGENT:
        runtime_dev = {pair.left.kind, pair.right.kind} & {KIND_DEV, KIND_TEST}
        if runtime_dev and {pair.left.kind, pair.right.kind} <= {
            KIND_RUNTIME,
            KIND_DEV,
            KIND_TEST,
        }:
            return Finding(
                id=RC205_RUNTIME_DEV_DIVERGENCE,
                title="Dependency declared differently in runtime and dev",
                severity=Severity.INFO,
                category=CATEGORY,
                message=(
                    f"'{name}' is declared differently for runtime and dev "
                    f"({pair.detail}); both environments install it, so the "
                    "difference is intentional only if it stays compatible."
                ),
                evidence=evidence,
                confidence=Confidence.MEDIUM,
            )
        return Finding(
            id=RC202_DUPLICATE_DECLARATION,
            title="Dependency declared more than once with different constraints",
            severity=Severity.WARNING,
            category=CATEGORY,
            message=(
                f"'{name}' is declared more than once with different constraints "
                f"({pair.detail}): {where}."
            ),
            evidence=evidence,
            confidence=Confidence.MEDIUM,
        )

    return None


def _origin(declaration: DependencyDeclaration) -> str:
    """A short, unambiguous location for a declaration."""
    if declaration.file and declaration.line:
        return f"{declaration.file}:{declaration.line} [{declaration.group}]"
    if declaration.file:
        return f"{declaration.file} [{declaration.source}:{declaration.group}]"
    return f"{declaration.source}:{declaration.group}"


def _pin_of(declaration: DependencyDeclaration) -> str:
    for part in declaration.specifier.split(","):
        if part.strip().startswith("=="):
            return part.strip()
    return declaration.specifier or "no constraint"


def _scope_label(pair: Pair) -> str:
    return {
        "runtime+dev": "same environment",
        "optional:x": "same extra",
        "optional-mixed": "extra and runtime",
        "constraint": "install constrained by the constraints file",
    }.get(pair.scope, pair.scope)


# --------------------------------------------------------------------------- #
# Specifier comparison
# --------------------------------------------------------------------------- #


def _parse_specifier(
    declaration: DependencyDeclaration,
) -> tuple[SpecifierSet | None, str | None]:
    text = declaration.specifier.strip()
    if not text:
        return (None, None)
    try:
        return (SpecifierSet(text), None)
    except InvalidSpecifier as exc:
        return (None, str(exc))


def _specifier_text(specifier: SpecifierSet | None) -> str:
    if specifier is None:
        return ""
    return ",".join(sorted(str(item) for item in specifier))


def _has_exclusion(specifier: SpecifierSet | None) -> bool:
    return bool(specifier) and any(item.operator == "!=" for item in specifier or [])


def _candidates(*specifiers: SpecifierSet | None) -> list[Version]:
    """Versions used to test whether two constraints can overlap.

    Candidates come from every version mentioned by the declarations plus the
    boundaries each operator implies. A conclusion is only reported when no
    candidate satisfies both constraints, and the evidence always states how
    many were tested.
    """
    raw: set[str] = set()
    for specifier in specifiers:
        if specifier is None:
            continue
        raw.update(_VERSION_TOKEN_RE.findall(str(specifier)))
        for item in specifier:
            if not item.version:
                continue
            raw.add(item.version)
            if item.operator in {"<", "<="}:
                raw.add(_previous_minor(item.version))
            if item.operator in {"!=", ">"}:
                raw.add(_next_patch(item.version))
    versions: set[Version] = set()
    for text in raw:
        for candidate in (text, f"{text}.0"):
            version = _as_version(candidate)
            if version is not None and not version.is_prerelease:
                versions.add(version)
    return sorted(versions)


def _has_common_version(
    candidates: list[Version], left: SpecifierSet | None, right: SpecifierSet | None
) -> bool:
    for candidate in candidates:
        if left is not None and candidate not in left:
            continue
        if right is not None and candidate not in right:
            continue
        return True
    return False


def _as_version(value: str) -> Version | None:
    try:
        return Version(value)
    except InvalidVersion:
        return None


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


def _next_patch(version: str) -> str:
    parts = version.split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        numbers = [int(part) for part in parts[:3]]
    except ValueError:  # pragma: no cover - defensive
        return version
    numbers[2] += 1
    return ".".join(str(number) for number in numbers)


# --------------------------------------------------------------------------- #
# Pinning strategy
# --------------------------------------------------------------------------- #


def _unbounded_runtime(
    declarations: list[DependencyDeclaration],
) -> list[Finding]:
    unbounded = sorted(
        {
            item.name
            for item in declarations
            if item.kind == KIND_RUNTIME and item.name and not item.is_bounded
        }
    )
    if not unbounded:
        return []
    return [
        Finding(
            id=RC203_UNBOUNDED_RUNTIME,
            title="Runtime dependency without a version constraint",
            severity=Severity.INFO,
            category=CATEGORY,
            message=(
                f"{len(unbounded)} runtime dependency/dependencies have no version "
                f"constraint: {', '.join(unbounded)}."
            ),
            evidence="; ".join(unbounded),
            confidence=Confidence.HIGH,
        )
    ]


def _mixed_pinning(declarations: list[DependencyDeclaration]) -> list[Finding]:
    runtime = [
        item
        for item in declarations
        if item.kind == KIND_RUNTIME and item.name and not item.is_external
    ]
    if len(runtime) < 3:
        return []
    pinned = {item.name for item in runtime if item.is_pinned}
    unbounded = {item.name for item in runtime if not item.is_bounded}
    ranged = {item.name for item in runtime if item.is_bounded and not item.is_pinned}
    if not pinned or not unbounded:
        return []
    if len(pinned) < 2:
        return []
    return [
        Finding(
            id=RC204_MIXED_PINNING,
            title="Mixed pinning strategy among runtime dependencies",
            severity=Severity.INFO,
            category=CATEGORY,
            message=(
                f"Runtime dependencies use {len(pinned)} exact pin(s), "
                f"{len(ranged)} range(s) and {len(unbounded)} without constraint. "
                "Unpinned: " + ", ".join(sorted(unbounded)) + "."
            ),
            evidence="; ".join(
                [
                    f"pinned: {', '.join(sorted(pinned))}",
                    f"ranged: {', '.join(sorted(ranged)) or '-'}",
                    f"unpinned: {', '.join(sorted(unbounded))}",
                ]
            ),
            confidence=Confidence.HIGH,
        )
    ]


# --------------------------------------------------------------------------- #
# Includes, external references, local paths
# --------------------------------------------------------------------------- #


def _missing_includes(includes: list[RequirementsInclude]) -> list[Finding]:
    findings: list[Finding] = []
    for include in includes:
        if include.exists is not False:
            continue
        if include.skipped_reason == "outside-project":
            continue
        identifier = (
            RC207_MISSING_CONSTRAINT_FILE
            if include.include_kind == "constraint"
            else RC206_MISSING_INCLUDE
        )
        title = (
            "Constraint file referenced but not found"
            if include.include_kind == "constraint"
            else "Requirements include referenced but not found"
        )
        findings.append(
            Finding(
                id=identifier,
                title=title,
                severity=Severity.WARNING,
                category=CATEGORY,
                message=(
                    f"{include.file}:{include.line} references '{include.value}', "
                    "which does not exist in the project."
                ),
                evidence=f"{include.file}:{include.line} -> {include.value}",
                file=include.file,
                line=include.line,
                confidence=Confidence.HIGH,
            )
        )
    return findings


def _external_references(
    declarations: list[DependencyDeclaration],
) -> list[Finding]:
    findings: list[Finding] = []
    for declaration in declarations:
        if declaration.reference_kind not in {REF_URL, REF_VCS}:
            continue
        origin = _origin(declaration)
        if declaration.reference_kind == REF_VCS:
            commit = declaration.vcs_commit
            if commit:
                findings.append(
                    Finding(
                        id=RC208_EXTERNAL_REFERENCE,
                        title="Dependency from a VCS repository pinned to a commit",
                        severity=Severity.INFO,
                        category=CATEGORY,
                        message=(
                            f"'{declaration.raw_name or declaration.raw}' is "
                            f"installed from a VCS reference pinned to commit "
                            f"{commit}."
                        ),
                        evidence=f"{origin}: {declaration.reference}",
                        file=declaration.file,
                        line=declaration.line,
                        confidence=Confidence.HIGH,
                    )
                )
                continue
            findings.append(
                Finding(
                    id=RC209_MUTABLE_VCS_REFERENCE,
                    title="Dependency from a mutable VCS reference",
                    severity=Severity.WARNING,
                    category=CATEGORY,
                    message=(
                        f"'{declaration.raw_name or declaration.raw}' is installed "
                        "from a VCS reference that is not pinned to a commit"
                        + (
                            f" (ref '{declaration.vcs_ref}')"
                            if declaration.vcs_ref
                            else " (no ref)"
                        )
                        + "."
                    ),
                    evidence=f"{origin}: {declaration.reference}",
                    file=declaration.file,
                    line=declaration.line,
                    confidence=Confidence.HIGH,
                )
            )
            continue

        findings.append(
            Finding(
                id=RC208_EXTERNAL_REFERENCE,
                title="Dependency installed from a direct URL",
                severity=Severity.INFO,
                category=CATEGORY,
                message=(
                    f"'{declaration.raw_name or declaration.raw}' relies on an "
                    "external URL instead of a package index."
                ),
                evidence=f"{origin}: {declaration.reference}",
                file=declaration.file,
                line=declaration.line,
                confidence=Confidence.HIGH,
            )
        )
    return findings


def _local_path_dependencies(
    declarations: list[DependencyDeclaration], root: Path | None
) -> list[Finding]:
    findings: list[Finding] = []
    for declaration in declarations:
        if declaration.reference_kind != REF_LOCAL_PATH or not declaration.reference:
            continue
        target = declaration.reference
        absolute = bool(_ABSOLUTE_PATH_RE.match(target))
        exists = None
        if root is not None and not absolute:
            candidate = (root / target).resolve()
            try:
                inside = candidate.is_relative_to(root.resolve())
            except (OSError, ValueError):  # pragma: no cover - defensive
                inside = False
            exists = inside and candidate.exists()
        if exists is True and not absolute:
            continue

        note = (
            " The path is absolute and local, so RC120 reports it as well."
            if absolute
            else ""
        )
        findings.append(
            Finding(
                id=RC210_LOCAL_PATH_DEPENDENCY,
                title="Dependency points to a local path",
                severity=Severity.WARNING,
                category=CATEGORY,
                message=(
                    f"'{declaration.raw_name or target}' is installed from the "
                    f"local path '{target}', which is not part of the "
                    f"repository.{note}"
                ),
                evidence=f"{_origin(declaration)}: {target}",
                file=declaration.file,
                line=declaration.line,
                confidence=Confidence.HIGH,
            )
        )
    return findings
