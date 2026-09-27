"""Project-level information collected from the scanned directory."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProjectScan:
    """Basic facts about the scanned directory."""

    name: str
    path: str
    exists: bool = True
    python_file_count: int = 0
    python_files_truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "path": self.path,
            "exists": self.exists,
            "python_file_count": self.python_file_count,
            "python_files_truncated": self.python_files_truncated,
        }
