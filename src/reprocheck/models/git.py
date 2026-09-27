"""Git metadata, collected with read-only commands only."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GitInfo:
    """Result of the read-only Git inspection.

    ``branch`` is ``None`` on a detached HEAD. ``head`` is the resolved commit
    SHA. ``is_clean`` is ``None`` when the state could not be determined.
    """

    is_repository: bool = False
    toplevel: str | None = None
    branch: str | None = None
    head: str | None = None
    is_clean: bool | None = None
    dirty_entries: int = 0
    git_available: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "is_repository": self.is_repository,
            "toplevel": self.toplevel,
            "branch": self.branch,
            "head": self.head,
            "is_clean": self.is_clean,
            "dirty_entries": self.dirty_entries,
            "git_available": self.git_available,
            "error": self.error,
        }
