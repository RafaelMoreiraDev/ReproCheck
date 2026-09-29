"""The verdict rules (V0.7).

Everything here reads an already-built report. No file is touched and no step
is re-run: the verdict is a pure derivation of facts and findings, so the same
report always produces the same verdict.

The rules are deliberately explicit rather than a weighted score:

* ``FAIL`` requires an observed failure of the reproduction itself. Static
  findings never produce ``FAIL`` on their own -- a project with a mutable CI
  action reference is not a project that failed to install.
* ``PASS`` requires a complete, successful reproduction *and* no open warning.
  A verified-but-uncertain project is ``PARTIAL``, not ``PASS``.
* Anything skipped is listed under ``not_verified`` instead of being counted
  as a failure.
"""

from __future__ import annotations

from reprocheck.models.finding import Finding, Severity
from reprocheck.models.report import ScanReport
from reprocheck.models.verdict import (
    ReproducibilityVerdict,
    UnverifiedItem,
    VerdictStatus,
)

#: Findings that prove a failure of the reproduction itself.
FAIL_FINDING_IDS = frozenset(
    {
        "RC400",  # pip check found a conflict
        "RC401",  # installation failed
        "RC402",  # required Python not installed
        "RC405",  # required Python ambiguous
        "RC406",  # the original project was modified
        "RC407",  # the virtual environment could not be created
        "RC500",  # an installed module failed to import
        "RC502",  # an import exceeded the timeout
    }
)

_FAIL_REASONS = {
    "RC400": "pip check reported broken requirements",
    "RC401": "the installation step failed",
    "RC402": "a required Python version is not installed",
    "RC405": "the required Python version is ambiguous",
    "RC406": "the original project was modified during the attempt",
    "RC407": "the virtual environment could not be created",
    "RC500": "a module provided by the distribution failed to import",
    "RC502": "a module import exceeded the per-module timeout",
}

#: True for every run, with the reason ReproCheck does not go further.
_ALWAYS_NOT_VERIFIED: tuple[UnverifiedItem, ...] = (
    UnverifiedItem(
        "full test suite",
        "ReproCheck never executes tests; only collection is attempted, and "
        "only with --runtime-checks",
    ),
    UnverifiedItem(
        "external datasets and model downloads",
        "never accessed; ReproCheck does not fetch data",
    ),
    UnverifiedItem(
        "remote URLs and VCS references",
        "not fetched; only their literal form is recorded",
    ),
    UnverifiedItem(
        "CI action revisions",
        "mutable action references are recorded but never resolved to a commit",
    ),
    UnverifiedItem(
        "published package index and network services",
        "installations resolve whatever the index serves at run time; no other "
        "service is contacted",
    ),
)

_SEVERITY_RANK = {
    Severity.ERROR: 0,
    Severity.WARNING: 1,
    Severity.INFO: 2,
}


def compute_verdict(report: ScanReport) -> ReproducibilityVerdict:
    """Return the overall verdict for ``report``."""
    not_verified = _not_verified(report)
    if not _attempted(report):
        return ReproducibilityVerdict(
            status=VerdictStatus.NOT_ATTEMPTED,
            reasons=(
                "no reproduction was attempted; only static facts were collected",
            ),
            not_verified=not_verified,
        )

    findings = all_findings(report)
    failures = _fail_reasons(findings)
    if failures:
        return ReproducibilityVerdict(
            status=VerdictStatus.FAIL, reasons=failures, not_verified=not_verified
        )

    reasons = _partial_reasons(report, findings)
    if reasons:
        return ReproducibilityVerdict(
            status=VerdictStatus.PARTIAL, reasons=reasons, not_verified=not_verified
        )
    return ReproducibilityVerdict(
        status=VerdictStatus.PASS, reasons=(), not_verified=not_verified
    )


def all_findings(report: ScanReport) -> list[Finding]:
    """Every finding of the report, static and reproduction, in ID order."""
    findings = report.findings + report.reproduction_findings
    return sorted(
        findings,
        key=lambda item: (_SEVERITY_RANK[item.severity], item.id, item.message),
    )


def _attempted(report: ScanReport) -> bool:
    return bool(report.reproduction) and bool(report.reproduction.get("attempted"))


def _fail_reasons(findings: list[Finding]) -> list[str]:
    ids = sorted({item.id for item in findings if item.id in FAIL_FINDING_IDS})
    return [f"{_FAIL_REASONS[ident]} ({ident})" for ident in ids]


def _partial_reasons(report: ScanReport, findings: list[Finding]) -> list[str]:
    """Everything that keeps a successful run from being a PASS."""
    reasons: list[str] = []
    warnings = sorted(
        {item.id for item in findings if item.severity is Severity.WARNING}
    )
    if warnings:
        reasons.append(f"{len(warnings)} open warning(s): {', '.join(warnings)}")

    reproduction = report.reproduction
    if not reproduction.get("installation"):
        reasons.append(
            "no installation was performed: " + _last_step(reproduction)  # type: ignore[arg-type]
        )

    runtime = reproduction.get("runtime_checks") or {}
    if not runtime.get("enabled"):
        reasons.append(
            "runtime checks were not requested, so module imports and test "
            "collection were not verified"
        )
        return reasons

    imports = runtime.get("imports") or []
    if not imports:
        reasons.append(
            "no importable module could be determined ("
            f"{runtime.get('import_discovery') or 'no target'})"
        )
    if not _all_imported(imports):
        failed = ", ".join(
            sorted(
                {
                    str(item.get("module"))
                    for item in imports
                    if not item.get("imported")
                }
            )
        )
        reasons.append(f"{len(failed)} module import(s) failed: {failed}")
    return reasons


def _all_imported(imports: list[dict[str, object]]) -> bool:
    return bool(imports) and all(item.get("imported") for item in imports)


def _last_step(reproduction: dict) -> str:
    steps = reproduction.get("completed_steps") or []
    return str(steps[-1]) if steps else "the attempt stopped early"


def _not_verified(report: ScanReport) -> tuple[UnverifiedItem, ...]:
    """What stayed unknown, kept strictly apart from what failed."""
    items: list[UnverifiedItem] = []
    if (report.conda or {}).get("declared"):
        items.append(
            UnverifiedItem(
                "Conda environment declaration",
                "a Conda environment was declared and its dependencies were "
                "read, but it was not reproduced: no conda process was run, no "
                "channel was contacted and no environment was solved",
            )
        )
    if not _attempted(report):
        items.append(
            UnverifiedItem(
                "reproduction",
                "no `reprocheck reproduce` run was requested; "
                "`reprocheck scan` collects static facts only",
            )
        )
        items.extend(_ALWAYS_NOT_VERIFIED)
        return tuple(items)

    runtime = report.reproduction.get("runtime_checks") or {}
    collection = runtime.get("pytest_collection") or {}
    if not runtime.get("enabled"):
        items.append(
            UnverifiedItem(
                "module imports and test collection",
                "runtime checks were not requested (--runtime-checks)",
            )
        )
    else:
        imports = runtime.get("imports") or []
        if not imports:
            items.append(
                UnverifiedItem(
                    "module imports",
                    "no importable module was determined: "
                    f"{runtime.get('import_discovery') or 'no target'}",
                )
            )
        if not collection.get("available"):
            items.append(
                UnverifiedItem(
                    "test collection",
                    str(
                        collection.get("reason")
                        or "pytest is not installed in the reproduced environment"
                    ),
                )
            )
        elif collection.get("ran"):
            items.append(
                UnverifiedItem(
                    "test outcomes",
                    f"{collection.get('collected')} test(s) were collected; "
                    "no test was executed",
                )
            )
    items.extend(_ALWAYS_NOT_VERIFIED)
    return tuple(items)
