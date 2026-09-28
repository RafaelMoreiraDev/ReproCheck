"""Suggestion outputs: ``reprocheck-suggestions.json`` and ``.md``.

Both describe proposals. Neither applies one, and the Markdown says so at the
top, because a reader who finds a diff in a file has to know nobody ran it.
"""

from __future__ import annotations

import json
from pathlib import Path

from reprocheck.output import SUGGESTIONS_MARKDOWN_NAME, SUGGESTIONS_NAME
from reprocheck.suggest.models import FixSuggestion, Safety, SuggestionReport

_TITLES = {
    Safety.SAFE: "Safe fixes",
    Safety.REVIEW_REQUIRED: "Review required",
    Safety.MANUAL_ONLY: "Manual investigation",
}

_COUNT_LABELS = (
    (Safety.SAFE, "safe fix available", "safe fixes available"),
    (Safety.REVIEW_REQUIRED, "requires review", "require review"),
    (Safety.MANUAL_ONLY, "needs manual investigation", "need manual investigation"),
)


def write_suggestions_json(report: SuggestionReport, destination: str | Path) -> Path:
    """Write the suggestions as JSON and return the resolved path."""
    path = Path(destination).expanduser()
    if path.is_dir():
        path = path / SUGGESTIONS_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
    path.write_text(payload + "\n", encoding="utf-8")
    return path.resolve()


def write_suggestions_markdown(
    report: SuggestionReport, destination: str | Path
) -> Path:
    """Write the suggestions as Markdown and return the resolved path."""
    path = Path(destination).expanduser()
    if path.is_dir():
        path = path / SUGGESTIONS_MARKDOWN_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_suggestions_markdown(report), encoding="utf-8")
    return path.resolve()


def render_suggestions_markdown(report: SuggestionReport) -> str:
    """Return the Markdown document of one suggestion run."""
    sections = [
        _header(),
        _summary(report),
        *_safety_sections(report),
        _declined(report),
        _technical(report),
    ]
    body = "\n\n".join(section for section in sections if section)
    return f"{body}\n"


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #


def _header() -> str:
    return (
        "# ReproCheck Fix Suggestions\n\n"
        "**Nothing here has been applied.** ReproCheck only proposes; the "
        "analysed project was not modified. `SAFE` means a local, additive, "
        "reversible text edit with no semantic choice in it -- not that a machine "
        "should apply it."
    )


def _summary(report: SuggestionReport) -> str:
    lines = [
        "## Summary",
        "",
        f"- Project: {report.project.get('name') or 'unknown'}",
        f"- Path: {report.project.get('path') or '-'}",
        f"- Findings examined: {report.summary.findings}",
        f"- Suggestions: {len(report.suggestions)}",
    ]
    for safety, singular, plural in _COUNT_LABELS:
        count = len(_bucket(report, safety))
        lines.append(f"- {count} {singular if count == 1 else plural}")
    lines.append(
        f"- {report.summary.no_proposal} problem(s) with no deterministic proposal"
    )
    lines.append(
        f"- {report.summary.informational} informational finding(s), not a problem "
        "to fix"
    )
    return "\n".join(lines)


def _bucket(report: SuggestionReport, safety: Safety) -> tuple[FixSuggestion, ...]:
    return tuple(item for item in report.suggestions if item.safety is safety)


def _safety_sections(report: SuggestionReport) -> list[str]:
    blocks: list[str] = []
    for safety in (Safety.SAFE, Safety.REVIEW_REQUIRED, Safety.MANUAL_ONLY):
        items = _bucket(report, safety)
        if not items:
            continue
        blocks.append(f"## {_TITLES[safety]}")
        blocks.append("\n\n".join(_suggestion(item) for item in items))
    return blocks


def _suggestion(item: FixSuggestion) -> str:
    lines = [
        f"### {item.suggestion_id}",
        "",
        f"- Finding: {item.finding_id}",
        f"- Safety: {item.safety.value}",
    ]
    if item.file:
        lines.append(f"- File: `{item.file}`")
    lines.append(f"- Confidence: {item.confidence}")
    if item.unified_diff:
        lines.append("- A patch could be generated later: yes")
    else:
        lines.append("- A patch could be generated later: no")
    lines.extend(["", item.title + ".", "", item.description])
    if item.unified_diff:
        lines.extend(["", "Proposed change:", "", "```diff", item.unified_diff, "```"])
    else:
        lines.extend(["", "No patch generated.", "", f"Reason: {item.rationale}"])
    if item.limitations:
        lines.extend(["", "Limits of this proposal:"])
        lines.extend(f"- {text}" for text in item.limitations)
    return "\n".join(lines)


def _declined(report: SuggestionReport) -> str:
    if not report.skipped:
        return ""
    lines = [
        "## No proposal",
        "",
        "These findings are problems that ReproCheck deliberately does not "
        "propose a fix for. They are listed so nothing disappears silently.",
        "",
    ]
    for item in report.skipped:
        location = f" (`{item.file}`)" if item.file else ""
        lines.append(f"- **{item.finding_id}**{location} — {item.reason}")
    return "\n".join(lines)


def _technical(report: SuggestionReport) -> str:
    from reprocheck.suggest.models import SUGGESTION_SCHEMA_VERSION

    return "\n".join(
        [
            "## Technical details",
            "",
            f"- Suggestion schema version: {SUGGESTION_SCHEMA_VERSION}",
            f"- ReproCheck version: {report.reprocheck_version}",
            f"- Scan timestamp: {report.scan_timestamp}",
            "- Every suggestion is deterministic: it comes from a fixed rule "
            "table, with no model, no inference and no network access",
            "- Patches are built in memory as unified diffs. No file of the "
            "analysed project was written, renamed or created",
        ]
    )
