"""Adversarial tests for the version-constraint proof engine.

Every test here exists to catch a false ``INCOMPATIBLE``. The engine may only
claim incompatibility with a proof; a missing witness must stay ``UNKNOWN``.
"""

from __future__ import annotations

import pytest

from reprocheck.checks.conflicts import Verdict, compare

# (left, right, expected verdict, why)
PROVABLY_COMPATIBLE = [
    (">=1,<10", ">=5,<6", "intervals overlap"),
    (">1,<1.0.2", "==1.0.1", "witness 1.0.1"),
    (">=1", "!=1.0", "witness above 1.0"),
    ("~=1.4", ">=1.4.5", "witness 1.4.5"),
    ("==1.*", ">=1.5", "witness 1.5"),
    (">=1,<3", ">=2,<4", "intervals overlap"),
    ("==1.2", ">=1.2", "witness 1.2"),
    ("==2.0.0", ">=1.0", "witness 2.0.0"),
    ("", ">=1", "an unbounded declaration accepts anything"),
]

PROVABLY_INCOMPATIBLE = [
    ("==1.2", "==1.3", "two different exact pins"),
    (">=2", "<2", "lower bound equals the exclusive upper bound"),
    (">2", "<=2", "exclusive lower bound against inclusive upper bound"),
    (">=2.1", "<2.1", "equal bounds, one exclusive"),
    (">=1,<2", ">=3", "disjoint intervals"),
    ("<2", ">=2", "disjoint intervals"),
    ("<=2", ">2", "disjoint intervals"),
    ("==1.0.0", ">=2.0.0", "exact pin below the range"),
]


@pytest.mark.parametrize(("left", "right", "why"), PROVABLY_COMPATIBLE)
def test_compatible_pairs(left: str, right: str, why: str) -> None:
    result = compare(left, right)

    assert result.verdict is Verdict.COMPATIBLE, f"{left} vs {right} ({why})"
    assert result.witness is not None, "a compatibility claim needs a witness"


@pytest.mark.parametrize(("left", "right", "why"), PROVABLY_INCOMPATIBLE)
def test_incompatible_pairs(left: str, right: str, why: str) -> None:
    result = compare(left, right)

    assert result.verdict is Verdict.INCOMPATIBLE, f"{left} vs {right} ({why})"
    assert result.detail, "an incompatibility claim needs a justification"


def test_exclusion_of_the_only_version_is_incompatible() -> None:
    result = compare("==2", "!=2")

    assert result.verdict is Verdict.INCOMPATIBLE
    assert "explicitly excluded" in result.detail


def test_exclusion_with_alternatives_is_not_incompatible() -> None:
    result = compare("==2", "!=3")

    assert result.verdict is Verdict.COMPATIBLE
    assert str(result.witness) in {"2", "2.0"}


def test_identical_constraints() -> None:
    result = compare(">=1.23,<2", "<2,>=1.23")

    assert result.verdict is Verdict.IDENTICAL


def test_unparsable_constraint_is_unknown() -> None:
    result = compare("not-a-specifier", ">=1")

    assert result.verdict is Verdict.UNKNOWN


def test_complex_operators_never_claim_incompatibility_without_proof() -> None:
    """No complex combination may end up as INCOMPATIBLE by exhaustion."""
    combinations = [
        ("~=1.4", "==1.5"),
        ("~=1.4", "<1.4"),
        ("==1.*", "==2.*"),
        ("!=1.0", "!=2.0"),
        ("~=2.0", ">=3"),
        ("==1.4.*", ">=1.5,<1.6"),
        (">=1,!=1.5", "<1.5"),
        ("~=1.4.2", "==1.4.1"),
    ]
    for left, right in combinations:
        result = compare(left, right)
        assert result.verdict is not Verdict.INCOMPATIBLE, f"{left} vs {right}"


def test_unknown_is_reported_as_such() -> None:
    result = compare("~=1.4", "==1.5")

    assert result.verdict in {Verdict.UNKNOWN, Verdict.COMPATIBLE}
    if result.verdict is Verdict.UNKNOWN:
        assert "no proof of incompatibility" in result.detail


def test_epoch_is_handled_like_any_other_version() -> None:
    result = compare("==1!2.0", ">=1!2.0")

    assert result.verdict is Verdict.COMPATIBLE
    assert compare("==1!2.0", "==2.0").verdict is Verdict.INCOMPATIBLE


def test_prerelease_bounds_are_not_confused() -> None:
    assert compare(">=2.0.0a1", "==2.0.0a1").verdict is Verdict.COMPATIBLE
    assert compare("==2.0.0a1", ">=2.0.0").verdict is Verdict.INCOMPATIBLE


def test_prerelease_exclusion_is_never_claimed_as_incompatible() -> None:
    """``<1.0.0`` excludes prereleases, but that is not provable here.

    PEP 440 makes ``<1.0.0`` reject ``1.0.0b1`` while ``>=1.0.0a1`` accepts it,
    so the intersection is empty for a reason no interval argument covers.
    ReproCheck must therefore answer UNKNOWN, not INCOMPATIBLE.
    """
    result = compare(">=1.0.0a1", "<1.0.0")

    assert result.verdict is Verdict.UNKNOWN


def test_detail_always_explains_the_verdict() -> None:
    for left, right in [
        ("==1.2", "==1.3"),
        (">=1", ">=2"),
        ("~=1.4", ">=1.4.5"),
    ]:
        assert compare(left, right).detail
