"""Audit records: one JSON file per real ``--apply`` attempt.

A record is written for every attempt that was allowed to reach the write
stage, including the ones that were refused afterwards (a stale file, a failed
validation, a rollback). A dry run writes no record and no backup: nothing
happened, so there is nothing to audit beyond the report.

Records never contain a secret: only paths, hashes, identifiers and the state
of the operation.
"""

from __future__ import annotations

import json
from pathlib import Path

from reprocheck.fix.models import ApplicationRecord
from reprocheck.output import project_output_dir

RECORD_SUFFIX = ".json"


def records_dir(root: str | Path, *, state_dir: Path | None = None) -> Path:
    """Where the records of one project live, inside its state directory."""
    return project_output_dir(root, root=state_dir) / "applied"


def record_path(
    root: str | Path, record_id: str, *, state_dir: Path | None = None
) -> Path:
    return records_dir(root, state_dir=state_dir) / f"{record_id}{RECORD_SUFFIX}"


def write_record(
    record: ApplicationRecord,
    root: str | Path,
    *,
    state_dir: Path | None = None,
) -> Path:
    """Persist ``record`` and return the written path."""
    path = record_path(root, record.record_id, state_dir=state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record.to_dict(), indent=2, ensure_ascii=False)
    path.write_text(payload + "\n", encoding="utf-8")
    return path.resolve()


def load_record(
    root: str | Path, record_id: str, *, state_dir: Path | None = None
) -> ApplicationRecord | None:
    """Return the record with this identifier, or ``None``."""
    path = record_path(root, record_id, state_dir=state_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "record_id" not in data:
        return None
    try:
        return ApplicationRecord.from_dict(data)
    except (KeyError, ValueError):
        return None


def list_records(
    root: str | Path, *, state_dir: Path | None = None
) -> tuple[ApplicationRecord, ...]:
    """Every record of this project, oldest identifier first."""
    directory = records_dir(root, state_dir=state_dir)
    if not directory.is_dir():
        return ()
    found: list[ApplicationRecord] = []
    for path in sorted(directory.glob(f"*{RECORD_SUFFIX}")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or "record_id" not in data:
            continue
        try:
            found.append(ApplicationRecord.from_dict(data))
        except (KeyError, ValueError):  # pragma: no cover - a corrupt file
            continue
    return tuple(found)
