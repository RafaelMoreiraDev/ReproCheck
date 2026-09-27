"""JSON report writer."""

from __future__ import annotations

import json
from pathlib import Path

from reprocheck.models import ScanReport

DEFAULT_REPORT_NAME = "reprocheck-report.json"


def write_report(report: ScanReport, destination: str | Path) -> Path:
    """Write ``report`` as UTF-8 JSON and return the resolved output path.

    This is the only write ReproCheck performs, and it always targets the
    explicit destination chosen by the user (or the current directory).
    """
    path = Path(destination).expanduser()
    if path.is_dir():
        path = path / DEFAULT_REPORT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
    path.write_text(payload + "\n", encoding="utf-8")
    return path.resolve()
