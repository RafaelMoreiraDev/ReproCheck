"""Baseline and comparison: factual differences between two reports."""

from reprocheck.diff.baseline import (
    BASELINE_SCHEMA_VERSION,
    Baseline,
    BaselineError,
    IdentityError,
    build_baseline,
    ensure_same_project,
    load_baseline,
    load_report,
    save_baseline,
)
from reprocheck.diff.engine import compare_reports
from reprocheck.diff.models import (
    DIFF_SCHEMA_VERSION,
    ChangeKind,
    FactChange,
    FindingChange,
    FindingChanges,
    ReportIdentity,
    ReproducibilityDiff,
    VerdictChange,
)

__all__ = [
    "BASELINE_SCHEMA_VERSION",
    "DIFF_SCHEMA_VERSION",
    "Baseline",
    "BaselineError",
    "IdentityError",
    "ChangeKind",
    "FactChange",
    "FindingChange",
    "FindingChanges",
    "ReportIdentity",
    "ReproducibilityDiff",
    "VerdictChange",
    "build_baseline",
    "compare_reports",
    "ensure_same_project",
    "load_baseline",
    "load_report",
    "save_baseline",
]
