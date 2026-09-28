"""Baselines: a saved report used as the reference of a comparison.

A baseline is **not** a blind copy. Before one is accepted it must prove that it
is a ReproCheck report: valid JSON, a known ``report_schema_version``, a
``reprocheck_version``, and the minimum keys the comparison reads. Anything else
is an operational error, never a silent comparison of two different things.

The file written by :func:`save_baseline` is a small wrapper::

    {
      "baseline_schema_version": "1",
      "saved_at": "2026-09-27T20:00:00+00:00",
      "reprocheck_version": "0.8.0",
      "report_schema_version": "6",
      "source": "C:\\\\...\\\\reprocheck-report.json",
      "project": { "name": ..., "path": ..., "head": ... },
      "report": { ...the full report, unchanged... }
    }

The report inside is stored verbatim: a baseline is evidence, not a projection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from reprocheck.diff.models import ReportIdentity
from reprocheck.output import project_identity
from reprocheck.scanner import REPORT_SCHEMA_VERSION

BASELINE_SCHEMA_VERSION = "1"

#: Report schemas this version can compare. Older ones are normalised by
#: absence: every domain the comparison reads is optional, so a missing section
#: simply produces no entries. Anything else is refused.
SUPPORTED_REPORT_SCHEMAS = ("1", "2", "3", "4", "5", "6")

#: Sections that do not exist in a given report schema.
SCHEMA_ABSENT_SECTIONS: dict[str, tuple[str, ...]] = {
    "1": ("dependencies", "workflow_references", "reproduction", "verdict"),
    "2": ("dependencies", "workflow_references", "reproduction", "verdict"),
    "3": ("workflow_references", "reproduction", "verdict"),
    "4": ("reproduction", "verdict"),
    "5": ("verdict",),
}

_MINIMUM_REPORT_KEYS = ("reprocheck_version", "report_schema_version", "project")


class BaselineError(Exception):
    """Raised when a baseline or report cannot be accepted."""


class IdentityError(BaselineError):
    """Raised when the two reports describe different projects."""


@dataclass(frozen=True, slots=True)
class Baseline:
    """A validated reference report."""

    report: dict
    baseline_schema_version: str = BASELINE_SCHEMA_VERSION
    saved_at: str | None = None
    source: str | None = None

    @property
    def report_schema_version(self) -> str:
        return str(self.report.get("report_schema_version"))

    @property
    def identity(self) -> ReportIdentity:
        return ReportIdentity.from_report(self.report)

    def absent_sections(self) -> tuple[str, ...]:
        return SCHEMA_ABSENT_SECTIONS.get(self.report_schema_version, ())

    def to_dict(self) -> dict[str, object]:
        identity = self.identity
        return {
            "baseline_schema_version": self.baseline_schema_version,
            "saved_at": self.saved_at,
            "reprocheck_version": self.report.get("reprocheck_version"),
            "report_schema_version": self.report.get("report_schema_version"),
            "source": self.source,
            "project": {
                "name": identity.name,
                "path": identity.path,
                "head": identity.head,
            },
            "report": self.report,
        }


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def read_json(path: str | Path) -> dict:
    """Read one JSON document, or raise an operational error."""
    location = Path(path).expanduser()
    try:
        text = location.read_text(encoding="utf-8")
    except OSError as exc:
        raise BaselineError(f"could not read {location}: {exc}") from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"{location} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise BaselineError(
            f"{location} does not contain a JSON object at the top level"
        )
    return document


def validate_report(document: dict, origin: str) -> dict:
    """Return ``document`` when it is a ReproCheck report, else raise."""
    missing = [key for key in _MINIMUM_REPORT_KEYS if key not in document]
    if missing:
        raise BaselineError(
            f"{origin} is not a ReproCheck report: missing {', '.join(missing)}"
        )
    if not isinstance(document.get("findings"), list):
        raise BaselineError(
            f"{origin} is not a ReproCheck report: 'findings' must be a list"
        )
    schema = str(document.get("report_schema_version"))
    if schema not in SUPPORTED_REPORT_SCHEMAS:
        raise BaselineError(
            f"{origin} uses report schema {schema!r}, which this version does not "
            f"understand; supported schemas are "
            f"{', '.join(SUPPORTED_REPORT_SCHEMAS)} and the current one is "
            f"{REPORT_SCHEMA_VERSION}. Re-run ReproCheck to produce a new report"
        )
    if not str(document.get("reprocheck_version")):
        raise BaselineError(
            f"{origin} has no reprocheck_version, so its producer is unknown"
        )
    return document


def load_baseline(path: str | Path) -> Baseline:
    """Load a baseline, accepting both the wrapper and a bare report."""
    location = Path(path).expanduser()
    document = read_json(location)

    if "baseline_schema_version" in document:
        version = str(document.get("baseline_schema_version"))
        if version != BASELINE_SCHEMA_VERSION:
            raise BaselineError(
                f"{location} uses baseline schema {version!r}, which this version "
                f"does not understand; the supported version is "
                f"{BASELINE_SCHEMA_VERSION}"
            )
        report = document.get("report")
        if not isinstance(report, dict):
            raise BaselineError(f"{location} is a baseline without a 'report' object")
        validate_report(report, str(location))
        return Baseline(
            report=report,
            saved_at=document.get("saved_at"),
            source=document.get("source"),
        )

    validate_report(document, str(location))
    return Baseline(report=document, source=str(location))


def load_report(path: str | Path) -> dict:
    """Load and validate a report document (the current side of a comparison)."""
    location = Path(path).expanduser()
    return validate_report(read_json(location), str(location))


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def save_baseline(
    report: dict,
    destination: str | Path,
    *,
    force: bool = False,
    source: str | Path | None = None,
    saved_at: str | None = None,
) -> Path:
    """Write ``report`` as a baseline, refusing to overwrite silently."""
    path = Path(destination).expanduser()
    if path.is_dir():
        path = path / "baseline.json"
    if path.exists() and not force:
        raise BaselineError(
            f"{path} already exists; pass --force to replace it deliberately"
        )
    document = Baseline(
        report=report,
        saved_at=saved_at or datetime.now(UTC).replace(microsecond=0).isoformat(),
        source=str(source) if source else None,
    ).to_dict()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, ensure_ascii=False)
    path.write_text(payload + "\n", encoding="utf-8")
    return path.resolve()


def build_baseline(report: dict, source: str | Path | None = None) -> Baseline:
    """Wrap a validated report in memory, without writing anything."""
    return Baseline(
        report=validate_report(report, str(source or "the given report")),
        source=str(source) if source else None,
    )


# --------------------------------------------------------------------------- #
# Project identity
# --------------------------------------------------------------------------- #


def ensure_same_project(baseline: dict, current: dict) -> None:
    """Refuse a comparison between two different projects.

    The identity is the normalised absolute path of the analysed project -- the
    same string the state directory is named after, so the two can never
    disagree. The Git HEAD is not part of the identity because it changes on
    every commit, and the project name alone is not enough because two
    repositories can share it.

    There is no override in V0.9: a comparison between two projects is a
    mistake, and a mistake should stop the command.
    """
    baseline_path = (baseline.get("project") or {}).get("path")
    current_path = (current.get("project") or {}).get("path")
    if not baseline_path or not current_path:
        missing = "baseline" if not baseline_path else "current"
        raise IdentityError(
            f"the {missing} report has no project path, so it cannot be proven to "
            f"describe the same project as the other one; re-run ReproCheck on "
            f"the project to produce a report with a path"
        )
    if project_identity(baseline_path) != project_identity(current_path):
        raise IdentityError(
            f"refusing to compare two different projects: the baseline describes "
            f"{baseline_path} and the current report describes {current_path}. "
            f"Use two reports of the same project, or save a new baseline for "
            f"this one"
        )
