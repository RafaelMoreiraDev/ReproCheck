"""Common interface for deterministic checks.

A check is a pure function of :class:`~reprocheck.facts.Facts`. It applies one
deterministic rule and returns the findings it can prove. Checks never read the
filesystem, never run code and never guess.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from reprocheck.facts import Facts
from reprocheck.models import Finding

CheckResult = Finding | Iterable[Finding | None] | None
Check = Callable[[Facts], CheckResult]


def run_checks(checks: Iterable[Check], facts: Facts) -> list[Finding]:
    """Run every check and return the findings, in check order.

    A check may return a single finding, an iterable of findings, or ``None``.
    """
    findings: list[Finding] = []
    for check in checks:
        result = check(facts)
        if result is None:
            continue
        candidates = [result] if isinstance(result, Finding) else list(result)
        findings.extend(item for item in candidates if item is not None)
    return findings
