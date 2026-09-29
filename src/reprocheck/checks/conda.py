"""Conda environment checks (RC230-RC235).

The rules here are deliberately narrow. A Conda environment is a legitimate way
to run Python, and almost every choice in one is a decision rather than a
defect, so none of these checks has an opinion about it:

* using ``defaults`` or ``conda-forge`` is not a finding;
* not pinning a dependency is not a finding;
* having a ``pip:`` subsection inside a Conda environment is not a finding;
* having no lockfile is not a finding.

What is a finding is where two declarations of the same fact disagree, and where
ReproCheck can **prove** the disagreement rather than merely observe different
text. The proof comes from :mod:`reprocheck.checks.conflicts`, which reports
``INCOMPATIBLE`` only when no version satisfies both sides. A difference that
cannot be decided is reported as compatible-with-unknown-bounds or not at all.
"""

from __future__ import annotations

import re

from reprocheck.checks.conflicts import Comparison, Verdict, compare
from reprocheck.facts import (
    CONDA_SOURCE_DEPENDENCY,
    KIND_BUILD,
    CondaDependency,
    CondaEnvironment,
    Facts,
)
from reprocheck.models import Confidence, Finding, Severity

RC230_CONDA_PYTHON_CONFLICT = "RC230"
RC231_CONDA_DEPENDENCY_CONFLICT = "RC231"
RC232_CONDA_DEPENDENCY_DIFFERS = "RC232"
RC233_CONDA_PIP_DUPLICATE = "RC233"
RC234_CONDA_UNREADABLE = "RC234"
RC235_CONDA_PIP_NOT_DECLARED = "RC235"

CONDA_CATEGORY = "conda"


def widen_wildcard_pin(specifier: str) -> str:
    """Rewrite a Conda wildcard pin as the equivalent interval, for reasoning.

    ``==3.10.*`` is exactly ``>=3.10,<3.11``: both accept the whole 3.10 series
    and neither accepts 3.11. :mod:`reprocheck.checks.conflicts` deliberately
    refuses to reason about a wildcard, because for a general ``==1.*`` the
    interval argument would need the version space to be enumerated.

    Here the wildcard is not general: it is always the translation of Conda's
    ``=X.Y``, so the upper bound is known exactly and the rewrite is an
    identity, not an approximation. Expressing it as an interval lets the
    existing, already-audited engine do the reasoning, and leaves that engine's
    conservatism untouched for every other caller.
    """
    parts: list[str] = []
    for clause in specifier.split(","):
        clause = clause.strip()
        match = _WILDCARD_PIN.match(clause)
        if match is None:
            parts.append(clause)
            continue
        components = match.group("version").split(".")
        if not all(part.isdigit() for part in components):
            parts.append(clause)
            continue
        if len(components) < 2:
            # ``==3.*`` is not a pin ReproCheck produces and its bound is not
            # implied, so it is left alone.
            parts.append(clause)
            continue
        upper = components[:-1] + [str(int(components[-1]) + 1)]
        parts.append(f">={match.group('version')},<{'.'.join(upper)}")
    return ",".join(parts)


#: ``==3.10.*`` -- an exact pin closed by Conda's wildcard.
_WILDCARD_PIN = re.compile(r"^==(?P<version>[0-9]+(?:\.[0-9]+)+)\.\*$")


def check_conda(facts: Facts) -> list[Finding]:
    """Apply every Conda rule."""
    findings: list[Finding] = []
    for environment in facts.conda_environments:
        findings.extend(_unreadable(environment))
        findings.extend(_python_conflict(facts, environment))
        findings.extend(_dependency_conflicts(facts, environment))
        findings.extend(_pip_duplicates(environment))
        findings.extend(_pip_not_declared(environment))
    return findings


# --------------------------------------------------------------------------- #
# RC234 - the file could not be read
# --------------------------------------------------------------------------- #


def _unreadable(environment: CondaEnvironment) -> list[Finding]:
    """Report a file that could not be understood at all.

    An ``error`` because the file is unambiguously a Conda environment by its
    name and ReproCheck has nothing to say about it: a scan that silently
    skipped it would claim to have inspected an environment it never read.
    """
    findings: list[Finding] = []
    if environment.parse_error is not None:
        findings.append(
            Finding(
                id=RC234_CONDA_UNREADABLE,
                title="Conda environment could not be read",
                severity=Severity.ERROR,
                category=CONDA_CATEGORY,
                message=(
                    f"{environment.file} is a Conda environment declaration but "
                    f"{environment.parse_error}, so nothing in it was checked."
                ),
                evidence=environment.parse_error,
                file=environment.file,
                confidence=Confidence.HIGH,
            )
        )
    for issue in environment.unsupported:
        findings.append(
            Finding(
                id=RC234_CONDA_UNREADABLE,
                title="Conda environment contains an unsupported structure",
                severity=Severity.WARNING,
                category=CONDA_CATEGORY,
                message=(
                    f"{environment.file}: {issue}. ReproCheck did not read it, "
                    "so it makes no claim about that part of the environment."
                ),
                evidence=issue,
                file=environment.file,
                confidence=Confidence.HIGH,
            )
        )
    return findings


# --------------------------------------------------------------------------- #
# RC230 - Conda Python versus the project's Python requirement
# --------------------------------------------------------------------------- #


def _python_conflict(facts: Facts, environment: CondaEnvironment) -> list[Finding]:
    """Compare the Conda Python pin with ``requires-python``.

    Only a *proven* incompatibility is a finding. ``python=3.11`` next to
    ``requires-python = ">=3.11"`` is not a conflict, and neither is
    ``python=3.11`` next to ``>=3.11,<4``, because no single version is
    excluded. A Conda pin that cannot be translated is silently skipped, since
    an undecidable pair is not evidence of anything.
    """
    declaration = environment.python_dependency
    if declaration is None or declaration.pep440_specifier is None:
        return []
    findings: list[Finding] = []
    for requirement in _project_python_requirements(facts):
        result = compare(
            requirement.value, widen_wildcard_pin(declaration.pep440_specifier)
        )
        if result.verdict is not Verdict.INCOMPATIBLE:
            continue
        findings.append(
            Finding(
                id=RC230_CONDA_PYTHON_CONFLICT,
                title="Conda Python version conflicts with the project requirement",
                severity=Severity.ERROR,
                category=CONDA_CATEGORY,
                message=(
                    f"{environment.file} pins Python with '{declaration.raw}', "
                    f"which is {declaration.pep440_specifier}, and "
                    f"{requirement.source} requires '{requirement.value}'. No "
                    f"version satisfies both: {result.detail}."
                ),
                evidence=f"{declaration.raw} vs {requirement.value}",
                file=environment.file,
                line=declaration.line,
                confidence=Confidence.HIGH,
            )
        )
    return findings


def _project_python_requirements(facts: Facts) -> list:
    """Only ``pyproject.toml``'s ``requires-python``.

    A CI matrix is deliberately excluded: it is expected to list several
    versions, and comparing it with a Conda pin would report a conflict every
    time a project tests a version the environment does not use.
    """
    return [
        item
        for item in facts.python_requirements
        if item.source.startswith("pyproject.toml") and item.value
    ]


# --------------------------------------------------------------------------- #
# RC231 / RC232 - dependencies
# --------------------------------------------------------------------------- #


def _dependency_conflicts(facts: Facts, environment: CondaEnvironment) -> list[Finding]:
    """Compare each Conda dependency with the pip metadata for the same name.

    Only the same package is compared, and only when both sides are
    unconditional. A dependency written ``# [win]`` applies to one platform and
    a pip requirement applies to all of them, so comparing them would invent a
    conflict that does not exist on any platform.
    """
    findings: list[Finding] = []
    by_name = _pip_declarations(facts)
    for dependency in environment.dependencies:
        counterparts = by_name.get(dependency.name)
        if not counterparts or dependency.is_conditional:
            continue
        for counterpart in counterparts:
            if counterpart.is_conditional:
                continue
            outcome = _compare(dependency, counterpart)
            if outcome.verdict is Verdict.INCOMPATIBLE:
                findings.append(
                    Finding(
                        id=RC231_CONDA_DEPENDENCY_CONFLICT,
                        title="A dependency is declared incompatibly in the Conda environment",
                        severity=Severity.ERROR,
                        category=CONDA_CATEGORY,
                        message=(
                            f"{environment.file} declares {dependency.name} as "
                            f"'{dependency.raw}' and {_where(counterpart)} declares "
                            f"'{counterpart.raw or counterpart.name}' for the same "
                            f"package. No version satisfies both: {outcome.detail}."
                        ),
                        evidence=(
                            f"{dependency.raw} vs {counterpart.raw or counterpart.name}"
                        ),
                        file=environment.file,
                        line=dependency.line,
                        confidence=Confidence.HIGH,
                    )
                )
                break
            if outcome.verdict is Verdict.COMPATIBLE:
                findings.append(
                    Finding(
                        id=RC232_CONDA_DEPENDENCY_DIFFERS,
                        title="A dependency is declared differently in the Conda environment",
                        severity=Severity.INFO,
                        category=CONDA_CATEGORY,
                        message=(
                            f"{environment.file} declares {dependency.name} as "
                            f"'{dependency.raw}' while {_where(counterpart)} declares "
                            f"'{counterpart.raw or counterpart.name}'. Both accept "
                            f"{outcome.detail}."
                        ),
                        evidence=(
                            f"{dependency.raw} vs {counterpart.raw or counterpart.name}"
                        ),
                        file=environment.file,
                        line=dependency.line,
                        confidence=Confidence.HIGH,
                    )
                )
    return findings


def _pip_declarations(facts: Facts) -> dict[str, list]:
    """Runtime-ish declarations from the pip metadata, keyed by normalised name.

    ``[build-system] requires`` is **excluded on purpose**. Those packages are
    installed in an isolated build environment used to assemble the wheel; they
    are not what runs, so a Conda development environment saying nothing about
    them is not a disagreement. The existing scope table already says a
    ``build`` declaration is never compared, and the first version of this check
    did compare it -- against tqdm, which lists ``setuptools`` in
    ``environment.yml`` and ``setuptools>=42`` in its build system. That
    produced an ``RC232`` about two unrelated things, and the regression test
    for it is ``test_build_system_requirements_are_never_compared``.

    Conda's own entries are not in this collection either: the scanner keeps
    them in the Conda facts, so adding them here as well would make every
    Conda dependency look like a duplicate of itself.
    """
    result: dict[str, list] = {}
    for declaration in facts.dependency_declarations:
        if not declaration.name or declaration.kind == KIND_BUILD:
            continue
        result.setdefault(declaration.name, []).append(declaration)
    return result


def _where(counterpart) -> str:
    """Name the section a counterpart came from, so scope is visible.

    ``ipywidgets>=6`` in a ``notebook`` extra and ``ipywidgets>=6`` in the
    runtime dependencies are different statements, and a reader cannot judge a
    divergence without knowing which one is being compared.
    """
    group = counterpart.group or counterpart.kind or "runtime"
    return f"{counterpart.source} [{group}]"


def _compare(dependency: CondaDependency, counterpart) -> Comparison:
    if dependency.pep440_specifier is None:
        return Comparison(
            Verdict.UNKNOWN,
            f"the Conda constraint '{dependency.raw_spec}' has no equivalent PEP 440 "
            "form, so no comparison is possible",
        )
    return compare(
        widen_wildcard_pin(dependency.pep440_specifier),
        widen_wildcard_pin(counterpart.specifier),
    )


# --------------------------------------------------------------------------- #
# RC233 - the same package twice in one environment
# --------------------------------------------------------------------------- #


def _pip_duplicates(environment: CondaEnvironment) -> list[Finding]:
    """Report a package that appears both as Conda and inside ``pip:``.

    An ``info``, not an error: the two installers are different, the resulting
    environment depends on which one wins, and ReproCheck cannot resolve that
    without running conda. It states the duplication and stops.
    """
    conda_names = {
        item.name
        for item in environment.dependencies
        if item.source == CONDA_SOURCE_DEPENDENCY
    }
    seen: dict[str, CondaDependency] = {}
    for item in environment.pip_dependencies:
        if item.name in conda_names and item.name not in seen:
            seen[item.name] = item
    return [
        Finding(
            id=RC233_CONDA_PIP_DUPLICATE,
            title="A package is declared both as Conda and as a pip requirement",
            severity=Severity.INFO,
            category=CONDA_CATEGORY,
            message=(
                f"{environment.file} lists {item.name} as a Conda dependency and "
                f"again inside the 'pip' subsection as '{item.raw}'. Which one "
                "provides the package depends on the order conda solves them; "
                "ReproCheck does not resolve it."
            ),
            evidence=f"{item.name} in both dependencies and pip",
            file=environment.file,
            line=item.line,
            confidence=Confidence.HIGH,
        )
        for item in seen.values()
    ]


# --------------------------------------------------------------------------- #
# RC235 - a pip subsection without pip
# --------------------------------------------------------------------------- #


def _pip_not_declared(environment: CondaEnvironment) -> list[Finding]:
    """Note a ``pip:`` subsection in an environment that does not list ``pip``.

    Purely an ``info``. It is a real observation and it is frequently benign --
    conda will install ``pip`` as a dependency of something else, and many
    projects do it deliberately -- so ReproCheck states it and moves on.
    """
    if not environment.has_pip_subsection or environment.declares_pip:
        return []
    return [
        Finding(
            id=RC235_CONDA_PIP_NOT_DECLARED,
            title="A pip subsection is present but pip is not a listed Conda dependency",
            severity=Severity.INFO,
            category=CONDA_CATEGORY,
            message=(
                f"{environment.file} has a 'pip' subsection with "
                f"{len(environment.pip_dependencies)} requirement(s) but does not "
                "list 'pip' among its Conda dependencies. This is often "
                "intentional and is not a defect; it is reported so the "
                "difference from the file's intent is visible."
            ),
            evidence="pip subsection without a pip dependency",
            file=environment.file,
            confidence=Confidence.HIGH,
        )
    ]


# Imported late to avoid a cycle in the type annotations above.
