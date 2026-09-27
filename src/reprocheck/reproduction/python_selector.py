"""Deterministic selection of a local Python interpreter.

The rules, in order, are:

1. an explicit ``.python-version`` wins, but only when it agrees with the
   project requirement;
2. otherwise the project requirement (``requires-python``) is used, and the
   **lowest locally installed version that satisfies it** is chosen, so the
   result does not drift with whatever happens to be newest on the machine;
3. otherwise the version declared by CI, when CI agrees on a single version.

If the sources disagree, or if no rule applies, nothing is chosen: the
reproduction is refused instead of guessing.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from reprocheck.facts import Facts
from reprocheck.reproduction.models import InterpreterCandidate, PythonSelection

#: ``3`` or ``3.11``: a series, not an exact release.
_SERIES_RE = re.compile(r"^\d+(\.\d+)?")

_VERSION_COMMAND = "import sys;print('%d.%d.%d' % sys.version_info[:3])"

#: Interpreter names probed on PATH when ``py -0p`` is unavailable.
FALLBACK_NAMES = (
    "python3.13",
    "python3.12",
    "python3.11",
    "python3.10",
    "python3",
    "python",
)


def discover_interpreters() -> list[InterpreterCandidate]:
    """Every distinct Python interpreter visible on this machine.

    Read-only: each candidate is asked for its version and nothing else.
    """
    found: dict[str, InterpreterCandidate] = {}
    for executable, source in _candidate_executables():
        version = _probe(executable)
        if version is None or version in found:
            continue
        found[version] = InterpreterCandidate(
            executable=executable, version=version, source=source
        )
    return [found[version] for version in sorted(found, key=_version_key)]


def select_python(facts: Facts) -> PythonSelection:
    """Choose the interpreter to reproduce with, or explain why none fits."""
    candidates = discover_interpreters()
    available = {item.version: item for item in candidates}

    pinned = _single_pin(facts)
    requirement = _project_requirement(facts)
    ci = _ci_versions(facts)

    if (
        pinned is not None
        and requirement is not None
        and not _pin_satisfies(pinned, requirement)
    ):
        return PythonSelection(
            selected=None,
            reason=(
                f".python-version selects {pinned} but the project requires "
                f"{requirement}; the two sources disagree"
            ),
            requirement=requirement,
            candidates=tuple(candidates),
            rejected=(),
        )

    if pinned is not None:
        pin = _pin_specifier(pinned)
        satisfying = _sorted_versions(
            [version for version in available if _in_specifier(version, pin)]
        )
        if not satisfying:
            return PythonSelection(
                selected=None,
                reason=(
                    f".python-version requires {pinned}, which is not installed "
                    f"locally (found: {', '.join(sorted(available)) or 'none'})"
                ),
                requirement=requirement or pinned,
                candidates=tuple(candidates),
            )
        chosen = satisfying[0]
        return PythonSelection(
            selected=chosen,
            executable=available[chosen].executable,
            reason=(
                f"lowest installed version matching .python-version {pinned} "
                f"({', '.join(satisfying)} available)"
            ),
            requirement=requirement,
            candidates=tuple(candidates),
        )

    if requirement is not None:
        satisfying = _sorted_versions(
            [version for version in available if _satisfies(version, requirement)]
        )
        if not satisfying:
            return PythonSelection(
                selected=None,
                reason=(
                    f"the project requires {requirement} and no installed "
                    "interpreter satisfies it"
                ),
                requirement=requirement,
                candidates=tuple(candidates),
            )
        chosen = satisfying[0]
        return PythonSelection(
            selected=chosen,
            executable=available[chosen].executable,
            reason=(
                f"lowest installed version satisfying {requirement} "
                f"({', '.join(satisfying)} available)"
            ),
            requirement=requirement,
            candidates=tuple(candidates),
        )

    if ci and len(ci) == 1 and ci[0] in available:
        chosen = ci[0]
        return PythonSelection(
            selected=chosen,
            executable=available[chosen].executable,
            reason="single Python version declared by CI",
            requirement=None,
            candidates=tuple(candidates),
        )

    if len(available) == 1:
        chosen = next(iter(available))
        return PythonSelection(
            selected=chosen,
            executable=available[chosen].executable,
            reason="the only Python interpreter installed and no requirement declared",
            requirement=None,
            candidates=tuple(candidates),
        )

    return PythonSelection(
        selected=None,
        reason=(
            "no version is declared and "
            f"{len(available)} interpreters are installed, so no choice is "
            "deterministic"
        ),
        requirement=None,
        candidates=tuple(candidates),
        rejected=tuple(sorted(ci)) if ci else (),
    )


# --------------------------------------------------------------------------- #
# Facts already collected by the scanner
# --------------------------------------------------------------------------- #


def _single_pin(facts: Facts) -> str | None:
    """The raw ``.python-version`` value, when the file declares one."""
    for item in facts.python_requirements:
        if item.source == ".python-version":
            return item.value.strip()
    return None


def _pin_specifier(pin: str) -> SpecifierSet | None:
    """Interpret a ``.python-version`` value as a specifier.

    A partial value is a series, not an exact release: ``3.14`` accepts every
    3.14.x, which is how the file is used in practice.
    """
    text = pin.strip()
    if not text:
        return None
    if _SERIES_RE.match(text):
        text = f"=={text}.*"
    else:
        text = f"=={text}"
    try:
        return SpecifierSet(text)
    except InvalidSpecifier:
        return None


def _project_requirement(facts: Facts) -> str | None:
    for item in facts.python_requirements:
        if not item.source.startswith("pyproject.toml"):
            continue
        if item.value and not _as_version(item.value):
            return item.value
    return None


def _ci_versions(facts: Facts) -> list[str]:
    versions = {
        version
        for item in facts.python_requirements
        if item.source.startswith(".github/")
        for version in [_as_version(item.value)]
        if version is not None
    }
    return sorted(versions, key=_version_key)


def _as_version(value: str) -> str | None:
    try:
        return str(Version(value.strip()))
    except InvalidVersion:
        return None


def _satisfies(version: str, requirement: str) -> bool:
    try:
        specifier = SpecifierSet(requirement)
    except InvalidSpecifier:
        return False
    return _in_specifier(version, specifier)


def _in_specifier(version: str, specifier: SpecifierSet | None) -> bool:
    if specifier is None:
        return False
    try:
        return Version(version) in specifier
    except InvalidVersion:  # pragma: no cover - defensive
        return False


def _pin_satisfies(pin: str, requirement: str) -> bool:
    """True when the ``.python-version`` series fits the project requirement."""
    specifier = _pin_specifier(pin)
    if specifier is None:
        return True
    try:
        project = SpecifierSet(requirement)
    except InvalidSpecifier:
        return True
    # A series and a range overlap when some version satisfies both; probing the
    # series boundaries is enough for the declarations seen in practice.
    for probe in _series_probes(specifier):
        if probe in project:
            return True
    return False


def _series_probes(specifier: SpecifierSet) -> list[Version]:
    """Concrete versions that stand in for a ``==X.Y.*`` series."""
    probes: list[Version] = []
    for item in specifier:
        if not item.version or item.operator != "==":
            continue
        base = item.version.replace(".*", "")
        for candidate in (base, f"{base}.0", f"{base}.1", f"{base}.99"):
            try:
                probes.append(Version(candidate))
            except InvalidVersion:  # pragma: no cover - defensive
                continue
    return probes


# --------------------------------------------------------------------------- #
# Interpreter discovery
# --------------------------------------------------------------------------- #


def _candidate_executables() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for executable in _py_launcher_executables():
        if executable.lower() not in seen and Path(executable).is_file():
            seen.add(executable.lower())
            found.append((executable, "py -0p"))
    for name in FALLBACK_NAMES:
        executable = shutil.which(name)
        if executable and executable.lower() not in seen:
            seen.add(executable.lower())
            found.append((executable, "PATH"))
    return found


def _py_launcher_executables() -> list[str]:
    """Ask the Windows ``py`` launcher which interpreters it knows."""
    launcher = shutil.which("py")
    if not launcher:
        return []
    try:
        completed = subprocess.run(  # noqa: S603
            [launcher, "-0p"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    executables: list[str] = []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if not parts or not parts[0].startswith("-V:"):
            continue
        for part in parts[1:]:
            if part.lower().endswith("python.exe") and Path(part).is_file():
                executables.append(part)
    return executables


def _probe(executable: str) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603
            [executable, "-c", _VERSION_COMMAND],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _version_key(value: str) -> tuple[int, ...]:
    try:
        return tuple(Version(value).release)
    except InvalidVersion:  # pragma: no cover - defensive
        return (0,)


def _sorted_versions(versions: list[str]) -> list[str]:
    return sorted(versions, key=_version_key)
