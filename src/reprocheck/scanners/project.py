"""Project-level scanning: name, absolute path and Python file count."""

from __future__ import annotations

import os
from pathlib import Path

from reprocheck.models import ProjectScan
from reprocheck.scanners.manifest import SKIP_DIRECTORIES

# Upper bound so that scanning a huge tree stays fast and bounded.
MAX_PYTHON_FILES = 20000


def scan_project(path: Path) -> ProjectScan:
    """Collect basic facts about ``path`` without modifying anything."""
    absolute = Path(os.path.abspath(path))
    count, truncated = _count_python_files(absolute)
    return ProjectScan(
        name=absolute.name or str(absolute),
        path=str(absolute),
        exists=absolute.is_dir(),
        python_file_count=count,
        python_files_truncated=truncated,
    )


def _count_python_files(root: Path) -> tuple[int, bool]:
    """Count ``*.py`` files, pruning noisy directories."""
    if not root.is_dir():
        return (0, False)
    count = 0
    for _current, dirnames, filenames in os.walk(root, onerror=None):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRECTORIES)
        count += sum(1 for name in filenames if name.endswith(".py"))
        if count >= MAX_PYTHON_FILES:
            return (count, True)
    return (count, False)
