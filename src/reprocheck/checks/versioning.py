"""Static check for versions derived from Git or VCS metadata (RC150).

A distribution whose version is computed from tags cannot be rebuilt from a
source tree alone, because the reproduction workspace deliberately excludes
``.git``. The declaration is objective: ``[project] dynamic`` contains
``version`` and a VCS-based provider is configured.

This check never claims that the build will fail, only that the version is not
derivable from the copied sources.
"""

from __future__ import annotations

from reprocheck.facts import Facts
from reprocheck.models import Confidence, Finding, Severity

RC150_GIT_DERIVED_VERSION = "RC150"

CATEGORY = "packaging"


def check_git_derived_version(facts: Facts) -> Finding | None:
    """Report a version that depends on Git/VCS metadata."""
    if "version" not in facts.dynamic_fields:
        return None
    if not facts.version_providers:
        return None

    return Finding(
        id=RC150_GIT_DERIVED_VERSION,
        title="Distribution version depends on Git or VCS metadata",
        severity=Severity.WARNING,
        category=CATEGORY,
        message=(
            f"'{facts.distribution_name or 'the project'}' declares a dynamic "
            "version provided from Git/VCS metadata ("
            + ", ".join(facts.version_providers)
            + "), but a reproduction workspace copies the sources without .git "
            "metadata, so the version cannot be derived from the copy."
        ),
        evidence=(
            f"dynamic = {list(facts.dynamic_fields)}; providers: "
            + "; ".join(facts.version_providers)
        ),
        file="pyproject.toml",
        confidence=Confidence.HIGH,
    )
