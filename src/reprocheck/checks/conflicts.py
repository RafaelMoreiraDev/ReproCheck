"""Deterministic version-constraint reasoning.

Three outcomes are distinguished, and the difference matters:

``INCOMPATIBLE``
    A **proof of incompatibility**: no version at all can satisfy both
    declarations. Only interval reasoning over simple operators may produce it.

``COMPATIBLE``
    A **witness of compatibility**: one concrete version satisfies both
    declarations. The witness is reported in the evidence.

``UNKNOWN``
    Neither proof nor witness. The declarations differ, but ReproCheck cannot
    decide, so no conflict may be claimed.

A finite candidate search can only ever produce a witness. Running out of
candidates proves nothing, so it yields ``UNKNOWN`` and never ``INCOMPATIBLE``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from packaging.specifiers import InvalidSpecifier, Specifier, SpecifierSet
from packaging.version import InvalidVersion, Version

# Operators that describe a plain interval and can therefore be reasoned about.
SIMPLE_OPERATORS = frozenset({"==", ">=", ">", "<=", "<"})

_VERSION_TOKEN_RE = re.compile(r"\d+(?:\.\d+)*")
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class Verdict(StrEnum):
    """What can be proven about two version constraints."""

    IDENTICAL = "identical"
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Bound:
    """One end of an interval."""

    version: Version
    inclusive: bool

    def below(self, other: Bound) -> bool:
        """True when no version can be at or above ``self`` and below ``other``."""
        if self.version > other.version:
            return True
        if self.version < other.version:
            return False
        return not (self.inclusive and other.inclusive)


@dataclass(frozen=True, slots=True)
class Interval:
    """The region of versions a set of simple specifiers accepts."""

    lower: Bound | None = None
    upper: Bound | None = None

    def is_disjoint_from(self, other: Interval) -> bool:
        if self.lower is not None and other.upper is not None:
            if self.lower.below(other.upper):
                return True
        if other.lower is not None and self.upper is not None:
            if other.lower.below(self.upper):
                return True
        return False


@dataclass(frozen=True, slots=True)
class Comparison:
    """The result of comparing two constraints, with its justification."""

    verdict: Verdict
    detail: str
    witness: Version | None = None


def parse_specifier(text: str) -> tuple[SpecifierSet | None, str | None]:
    """Parse ``text``; return ``(None, error)`` when it is not a specifier set."""
    cleaned = text.strip()
    if not cleaned:
        return (None, None)
    try:
        return (SpecifierSet(cleaned), None)
    except InvalidSpecifier as exc:
        return (None, str(exc))


def normalise(specifier: SpecifierSet | None) -> str:
    """A stable textual form, so two constraints can be compared for equality."""
    if specifier is None:
        return ""
    return ",".join(sorted(str(item) for item in specifier))


def is_simple(specifier: SpecifierSet | None) -> bool:
    """True when every clause is a simple, non-wildcard comparison.

    ``!=``, ``~=`` and wildcard equality are excluded on purpose: they carve
    holes in the accepted set, so an interval argument would be unsound.
    """
    if specifier is None:
        return False
    for item in specifier:
        if item.operator not in SIMPLE_OPERATORS:
            return False
        if "*" in item.version:
            return False
        if not item.version:
            return False
    return True


def exact_pin(specifier: SpecifierSet | None) -> Version | None:
    """Return the single version accepted, when the set is one exact pin."""
    if specifier is None or len(specifier) != 1:
        return None
    item = next(iter(specifier))
    if item.operator != "==" or "*" in item.version:
        return None
    return _as_version(item.version)


def excludes(specifier: SpecifierSet | None, version: Version) -> bool:
    """True when an explicit ``!=`` clause rejects ``version``."""
    if specifier is None:
        return False
    for item in specifier:
        if item.operator != "!=" or not item.version:
            continue
        try:
            equality = Specifier(f"=={item.version}")
        except InvalidSpecifier:  # pragma: no cover - defensive
            continue
        if equality.contains(version, prereleases=True):
            return True
    return False


def interval(specifier: SpecifierSet | None) -> Interval | None:
    """Return the interval accepted by ``specifier``, or ``None`` if unknown."""
    if not is_simple(specifier):
        return None
    assert specifier is not None
    lower: Bound | None = None
    upper: Bound | None = None
    for item in specifier:
        version = _as_version(item.version)
        if version is None:
            return None
        if item.operator == "==":
            # Exact equality pins both ends of the interval.
            exact = Bound(version, True)
            lower = _lower_of(lower, exact)
            upper = _upper_of(upper, exact)
        elif item.operator in {">", ">="}:
            lower = _lower_of(lower, Bound(version, item.operator == ">="))
        elif item.operator in {"<", "<="}:
            upper = _upper_of(upper, Bound(version, item.operator == "<="))
    return Interval(lower=lower, upper=upper)


def _lower_of(current: Bound | None, candidate: Bound) -> Bound:
    if current is None or _tighter_lower(candidate, current):
        return candidate
    return current


def _upper_of(current: Bound | None, candidate: Bound) -> Bound:
    if current is None or _tighter_upper(candidate, current):
        return candidate
    return current


def _tighter_lower(candidate: Bound, current: Bound) -> bool:
    return candidate.version > current.version or (
        candidate.version == current.version and not candidate.inclusive
    )


def _tighter_upper(candidate: Bound, current: Bound) -> bool:
    return candidate.version < current.version or (
        candidate.version == current.version and not candidate.inclusive
    )


def compare(left: str, right: str) -> Comparison:
    """Compare two version constraint strings.

    The order of the reasoning is deliberate: identity, then a proof of
    incompatibility, then a witness of compatibility, then ``UNKNOWN``.
    """
    left_set, left_error = parse_specifier(left)
    right_set, right_error = parse_specifier(right)
    if left_error or right_error:
        return Comparison(
            Verdict.UNKNOWN, f"unparsable specifier: {left_error or right_error}"
        )

    if normalise(left_set) == normalise(right_set):
        return Comparison(Verdict.IDENTICAL, "identical version constraint")

    left_interval = interval(left_set)
    right_interval = interval(right_set)
    if left_interval is not None and right_interval is not None:
        if left_interval.is_disjoint_from(right_interval):
            return Comparison(
                Verdict.INCOMPATIBLE,
                "the two intervals do not overlap: "
                f"{_describe_interval(left_interval)} vs "
                f"{_describe_interval(right_interval)}",
            )

    # A narrow but sound proof: one declaration accepts a single version and
    # the other explicitly excludes it.
    for pin_set, other_set in ((left_set, right_set), (right_set, left_set)):
        pin = exact_pin(pin_set)
        if pin is not None and excludes(other_set, pin):
            return Comparison(
                Verdict.INCOMPATIBLE,
                f"the only version accepted by one declaration ({pin}) is "
                "explicitly excluded by the other",
            )

    witness = find_witness(left_set, right_set)
    if witness is not None:
        return Comparison(
            Verdict.COMPATIBLE,
            f"version {witness} satisfies both",
            witness=witness,
        )

    return Comparison(
        Verdict.UNKNOWN,
        "no witness found and no proof of incompatibility; the constraints "
        f"differ ('{left}' vs '{right}')",
    )


def find_witness(
    left: SpecifierSet | None, right: SpecifierSet | None
) -> Version | None:
    """Return one version accepted by both, or ``None``.

    Finding a witness proves compatibility. Not finding one proves nothing:
    the candidate set is finite, the version space is not.
    """
    for candidate in _candidates(left, right):
        if left is not None and candidate not in left:
            continue
        if right is not None and candidate not in right:
            continue
        return candidate
    return None


def _candidates(left: SpecifierSet | None, right: SpecifierSet | None) -> list[Version]:
    """Versions worth testing: every token mentioned, plus operator boundaries."""
    seeds: set[str] = set()
    allow_prerelease = False
    for specifier in (left, right):
        if specifier is None:
            continue
        seeds.update(_VERSION_TOKEN_RE.findall(str(specifier)))
        for item in specifier:
            version = _as_version(item.version)
            if version is None:
                continue
            if version.is_prerelease:
                allow_prerelease = True
            seeds.update(_boundaries(item))

    versions: set[Version] = set()
    for text in seeds:
        for candidate in _expand(text, allow_prerelease):
            version = _as_version(candidate)
            if version is None:
                continue
            if version.is_prerelease and not allow_prerelease:
                continue
            versions.add(version)
    return sorted(versions)


def _expand(text: str, allow_prerelease: bool) -> list[str]:
    expanded = [text, f"{text}.0"]
    if allow_prerelease:
        expanded += [f"{text}a1", f"{text}b1", f"{text}rc1"]
    return expanded


def _boundaries(item: Specifier) -> set[str]:
    """Versions implied by one clause, used as witness candidates."""
    if not item.version:
        return set()
    found = {item.version}
    if item.operator in {"<", "<="}:
        found.add(_previous_minor(item.version))
    if item.operator in {"!=", ">", "=="}:
        found.add(_next_patch(item.version))
    if item.operator == "~=":
        found.add(_next_minor(item.version))
    if "*" in item.version:
        # ``==1.2.*`` accepts everything from 1.2 upwards.
        base = item.version.replace("*", "0")
        found.add(base)
        found.add(_next_minor(base))
    # Micro versions make dense intervals reachable, e.g. ``>1`` with ``<1.0.2``.
    found.update(f"{item.version}.{micro}" for micro in ("0", "1", "2"))
    return found


def _as_version(value: str) -> Version | None:
    try:
        return Version(value)
    except InvalidVersion:
        return None


def _previous_minor(version: str) -> str:
    numbers = _numbers(version)
    if numbers is None:
        return version
    if numbers[1] > 0:
        numbers[1] -= 1
    elif numbers[0] > 0:
        numbers[0] -= 1
    else:
        return version
    numbers[2] = 0
    return ".".join(str(number) for number in numbers)


def _next_minor(version: str) -> str:
    numbers = _numbers(version)
    if numbers is None:
        return version
    numbers[1] += 1
    numbers[2] = 0
    return ".".join(str(number) for number in numbers)


def _next_patch(version: str) -> str:
    numbers = _numbers(version)
    if numbers is None:
        return version
    numbers[2] += 1
    return ".".join(str(number) for number in numbers)


def _numbers(version: str) -> list[int] | None:
    parts = version.split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        return [int(part) for part in parts[:3]]
    except ValueError:
        return None


def _describe_interval(value: Interval) -> str:
    if value.lower is None and value.upper is None:
        return "any version"
    if value.lower is None:
        return _bound_text(value.upper, lower=False)  # type: ignore[arg-type]
    if value.upper is None:
        return _bound_text(value.lower, lower=True)
    return f"[{_bound_text(value.lower, lower=True)}, {_bound_text(value.upper, lower=False)}]"


def _bound_text(bound: Bound, *, lower: bool) -> str:
    if lower:
        return f">= {bound.version}" if bound.inclusive else f"> {bound.version}"
    return f"<= {bound.version}" if bound.inclusive else f"< {bound.version}"
