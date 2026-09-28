"""Baseline comparison outputs: ``reprocheck-diff.json`` and ``reprocheck-diff.md``.

Same contract as the report writers: the JSON is the structured source, the
Markdown is a projection for humans, sections that would be empty are omitted,
and nothing is written anywhere except the given destination.
"""

from __future__ import annotations

import json
from pathlib import Path

from reprocheck.diff.models import (
    DIFF_SCHEMA_VERSION,
    ChangeKind,
    FactChange,
    FindingChange,
    ReproducibilityDiff,
)

_DOMAIN_TITLE = {
    "dependency": "Dependencies",
    "python": "Python",
    "ci": "CI",
    "reproduction": "Reproduction",
}


def write_diff_json(diff: ReproducibilityDiff, destination: str | Path) -> Path:
    """Write the comparison as JSON and return the resolved path."""
    path = Path(destination).expanduser()
    if path.is_dir():
        path = path / "reprocheck-diff.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(diff.to_dict(), indent=2, ensure_ascii=False)
    path.write_text(payload + "\n", encoding="utf-8")
    return path.resolve()


def write_diff_markdown(diff: ReproducibilityDiff, destination: str | Path) -> Path:
    """Write the comparison as Markdown and return the resolved path."""
    path = Path(destination).expanduser()
    if path.is_dir():
        path = path / "reprocheck-diff.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_diff_markdown(diff), encoding="utf-8")
    return path.resolve()


def render_diff_markdown(diff: ReproducibilityDiff) -> str:
    """Return the Markdown document of one comparison."""
    sections = [
        _header(diff),
        _summary(diff),
        _verdict(diff),
        _new_problems(diff),
        _resolved_problems(diff),
        _changed_findings(diff),
        _changed_facts(diff),
        _technical(diff),
    ]
    body = "\n\n".join(section for section in sections if section)
    return f"{body}\n"


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #


def _header(diff: ReproducibilityDiff) -> str:
    return (
        "# ReproCheck Baseline Comparison\n\n"
        "A factual comparison of two ReproCheck reports. It states what changed; "
        "it does not judge whether the change is good news."
    )


def _summary(diff: ReproducibilityDiff) -> str:
    lines = [
        "## Summary",
        "",
        _identity_line("Baseline", diff.baseline),
        _identity_line("Current", diff.current),
        "",
    ]
    if diff.verdict.changed:
        lines.append(
            f"Verdict: {diff.verdict.previous or '(none)'} → "
            f"{diff.verdict.current or '(none)'}"
        )
    else:
        lines.append(f"Verdict: {diff.verdict.current or '(none)'} (no change)")
    lines.append("")
    lines.append(f"Material changes: {diff.material_change_count}")
    return "\n".join(lines)


def _identity_line(label: str, identity) -> str:
    parts = [f"- {label}: {identity.name or 'unknown project'}"]
    if identity.path:
        parts.append(f"  path `{identity.path}`")
    if identity.head:
        parts.append(f"  commit `{identity.head[:12]}`")
    if identity.scan_timestamp:
        parts.append(f"  scanned {identity.scan_timestamp}")
    if identity.reprocheck_version:
        parts.append(f"  produced by reprocheck {identity.reprocheck_version}")
    if identity.report_schema_version:
        parts.append(f"  report schema {identity.report_schema_version}")
    return "\n".join(parts)


def _verdict(diff: ReproducibilityDiff) -> str:
    if not diff.verdict.changed:
        return ""
    lines = [
        "## Verdict",
        "",
        f"The verdict changed from {diff.verdict.previous or '(none)'} to "
        f"{diff.verdict.current or '(none)'}.",
    ]
    if diff.verdict.note:
        lines.append("")
        lines.append(diff.verdict.note + ".")
    lines.append("")
    lines.append(
        "ReproCheck does not classify this movement as an improvement or a "
        "regression: the two reports describe different points in time, and the "
        "verdict rules are in `README.md`."
    )
    return "\n".join(lines)


def _finding_block(item: FindingChange) -> str:
    finding = item.current or item.previous or {}
    marker = {"added": "NEW", "resolved": "RESOLVED", "changed": "CHANGED"}[
        item.kind.value
    ]
    lines = [
        "```text",
        f"{finding.get('id', '?')} — {marker} IN THE COMPARISON",
        "",
        f"{finding.get('title', '')}.",
        "",
        str(finding.get("message", "")),
    ]
    if item.kind is ChangeKind.CHANGED and item.changed_fields:
        lines.extend(["", "Changed: " + ", ".join(item.changed_fields)])
    if finding.get("evidence"):
        lines.extend(["", "Evidence:", f"  {finding['evidence']}"])
    if finding.get("file"):
        lines.append("")
        lines.append(f"Location: {finding['file']}")
    lines.append("```")
    return "\n".join(lines)


def _new_problems(diff: ReproducibilityDiff) -> str:
    if not diff.findings.added:
        return ""
    return _finding_section("## New problems", diff.findings.added)


def _resolved_problems(diff: ReproducibilityDiff) -> str:
    if not diff.findings.resolved:
        return ""
    return _finding_section("## Resolved problems", diff.findings.resolved)


def _changed_findings(diff: ReproducibilityDiff) -> str:
    if not diff.findings.changed:
        return ""
    return _finding_section("## Changed problems", diff.findings.changed)


def _finding_section(title: str, items: tuple[FindingChange, ...]) -> str:
    return f"{title}\n\n" + "\n\n".join(_finding_block(item) for item in items)


def _changed_facts(diff: ReproducibilityDiff) -> str:
    groups = (
        ("dependency", diff.dependencies),
        ("python", diff.python),
        ("ci", diff.ci),
        ("reproduction", diff.reproduction),
    )
    blocks: list[str] = []
    for domain, changes in groups:
        if not changes:
            continue
        lines = [f"### {_DOMAIN_TITLE[domain]}", ""]
        lines.extend(_fact_line(item) for item in changes)
        blocks.append("\n".join(lines))
    if not blocks:
        return ""
    return "## Changed facts\n\n" + "\n\n".join(blocks)


def _fact_line(item: FactChange) -> str:
    if item.kind is ChangeKind.CHANGED:
        fields = ", ".join(item.changed_fields or ("value",))
        return f"- **{item.label}** — {fields}: `{item.previous}` → `{item.current}`"
    if item.kind is ChangeKind.ADDED:
        return f"- **added** {item.label}" + (
            f" — `{item.current}`" if item.current else ""
        )
    return f"- **resolved** {item.label}" + (
        f" — `{item.previous}`" if item.previous else ""
    )


def _technical(diff: ReproducibilityDiff) -> str:
    counts = {
        "findings added": len(diff.findings.added),
        "findings resolved": len(diff.findings.resolved),
        "findings changed": len(diff.findings.changed),
        "dependencies": len(diff.dependencies),
        "python declarations": len(diff.python),
        "CI references": len(diff.ci),
        "reproduction facts": len(diff.reproduction),
    }
    lines = [
        "## Technical details",
        "",
        f"- Diff schema version: {DIFF_SCHEMA_VERSION}",
        "- Volatile fields ignored: timestamps, durations, temporary workspace and "
        "run ids, log paths, interpreter executables, list order and line numbers",
        "- Comparison is a pure function of the two reports: no step was re-run and "
        "no project was touched",
        "",
    ]
    lines.extend(f"- {name}: {count}" for name, count in counts.items())
    return "\n".join(lines)
