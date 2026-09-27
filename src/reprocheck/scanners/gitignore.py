"""Reading of the project ``.gitignore``.

Only the root ``.gitignore`` is inspected in V0.2. The content is parsed as a
flat list of normalised patterns; no glob engine is involved.
"""

from __future__ import annotations

import re
from pathlib import Path

from reprocheck.facts import GitignoreInfo, normalise_pattern

GITIGNORE_FILE = ".gitignore"

_ESCAPED_COMMENT_RE = re.compile(r"(?<!\\)#.*$")


def scan_gitignore(root: Path) -> GitignoreInfo:
    """Parse the root ``.gitignore``, if present."""
    path = root / GITIGNORE_FILE
    if not path.is_file():
        return GitignoreInfo(exists=False)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return GitignoreInfo(exists=False)
    return GitignoreInfo(exists=True, patterns=tuple(_parse(text)))


def _parse(text: str) -> list[str]:
    patterns: list[str] = []
    for raw in text.splitlines():
        line = _ESCAPED_COMMENT_RE.sub("", raw).strip()
        if not line or line.startswith("!"):
            continue
        normalized = normalise_pattern(line)
        if normalized and normalized not in patterns:
            patterns.append(normalized)
    return patterns
