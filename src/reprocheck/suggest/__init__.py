"""Deterministic fix suggestions: proposals only, never actions."""

from reprocheck.suggest.engine import (
    GITIGNORE,
    SuggestionContext,
    suggest,
    supported_findings,
)
from reprocheck.suggest.models import (
    SUGGESTION_SCHEMA_VERSION,
    FixSuggestion,
    Safety,
    SkippedFinding,
    SuggestionKind,
    SuggestionReport,
    SuggestionSummary,
)

__all__ = [
    "GITIGNORE",
    "SUGGESTION_SCHEMA_VERSION",
    "FixSuggestion",
    "Safety",
    "SkippedFinding",
    "SuggestionContext",
    "SuggestionKind",
    "SuggestionReport",
    "SuggestionSummary",
    "suggest",
    "supported_findings",
]
