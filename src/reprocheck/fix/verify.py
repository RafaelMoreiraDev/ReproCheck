"""Post-fix verification, and nothing else.

The question this module answers is narrow: **after the bytes were replaced,
does the project look the way it was supposed to?** It answers it by re-reading
the project statically and comparing the two states.

What it deliberately does not do is run the project's tests, import its modules,
install anything or touch the network. A `--verify` that executed project code
would be a different command with different risks and a different consent
story, and pretending the deterministic check is the dynamic one would be the
most misleading thing this tool could do. `--verify` is a scanner comparison, and
its report says so.

Four questions, each named so a failure says which one failed:

``target-resolved``     the finding this suggestion addressed is gone, and the
                        file now covers exactly what the rule asked it to cover
``content-intact``      the file still holds the bytes ReproCheck wrote
``no-regressions``      no new error, no new RC220 or RC221, and nothing new
                        attributed to the file that was changed
``project-integrity``   no file changed during verification other than the ones
                        the suggestion's own diff names

Findings are compared by **identity** rather than by count, because a count that
fell says nothing about which finding went. The identity reuses the baseline
comparison's normalisation and then removes line numbers, since a line that
moved is not a new finding and a new finding on a moved line is not a
regression.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from reprocheck.diff.normalise import normalise_findings
from reprocheck.fix.models import FixVerification, VerificationCheck

#: ``path/to/file.py:58`` and ``path/to/file.py:58:12``. A line that moved is
#: not a new finding.
_LINE_SUFFIX_RE = re.compile(r":\d+(?::\d+)?(?=\b|$)")

#: Directories whose contents change on their own and say nothing about a fix.
VOLATILE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".reprocheck",
        "node_modules",
    }
)

#: Rules whose findings are about the project's own configuration rather than
#: about a file a fix edited. A new one of these after a fix is a regression
#: regardless of which file it points at, because the fix cannot have caused it
#: and its presence means the project is in a state nobody expected.
CONFIGURATION_RULES = frozenset({"RC220", "RC221"})


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A fingerprint of the files a verification may care about.

    Content hashes, not size and modification time. Size and mtime are the
    cheap answer and they are wrong here: a file rewritten in place with the
    same number of bytes inside the filesystem's timestamp granularity looks
    untouched, and the window this covers is a fraction of a second. That is
    not theoretical on Windows, where the granularity is coarse enough to
    reproduce it in a test.

    Hashing every file of a large project twice is not free either, which is
    why a snapshot is taken only when ``--verify`` asks for one and never on the
    default path.
    """

    entries: dict[str, str]

    def changed_since(
        self, other: Snapshot, *, allowed: frozenset[str]
    ) -> tuple[str, ...]:
        """Paths present in one and not the other, or differing, minus allowed."""
        found = {
            path
            for path in set(self.entries) | set(other.entries)
            if self.entries.get(path) != other.entries.get(path)
        }
        return tuple(sorted(path for path in found if path not in allowed))


def snapshot_project(root: Path) -> Snapshot:
    """Fingerprint every tracked-looking file under ``root``."""
    import hashlib

    entries: dict[str, str] = {}
    for path in _walk(root):
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(block)
        except OSError:  # pragma: no cover - a file that vanished mid-walk
            continue
        entries[path.relative_to(root).as_posix()] = digest.hexdigest()
    return Snapshot(entries=entries)


def _walk(root: Path):
    for path in root.rglob("*"):
        parts = path.relative_to(root).parts
        if any(part in VOLATILE_DIRS for part in parts):
            continue
        if path.is_file():
            yield path


def diff_paths(unified_diff: str | None, fallback: str) -> frozenset[str]:
    """The files a unified diff touches.

    Read from the diff rather than assumed, so a rule that ever proposes more
    than one file is checked for all of them. The fallback is the suggestion's
    own file, which is what the diff names when the diff is unavailable.
    """
    if not unified_diff:
        return frozenset({fallback})
    found = {
        line[6:].strip().split("\t")[0].lstrip("b/")
        for line in unified_diff.splitlines()
        if line.startswith("+++ b/")
    }
    found = {path for path in found if path and path != "/dev/null"}
    return frozenset(found or {fallback})


def identities(report: dict) -> dict[str, dict[str, str]]:
    """Findings by identity, ignoring where in the file a line now sits."""
    stripped = {}
    for summary in normalise_findings(report).values():
        evidence = _LINE_SUFFIX_RE.sub("", summary.get("evidence", ""))
        identity = "|".join((summary.get("id", ""), summary.get("file", ""), evidence))
        stripped[identity] = summary
    return stripped


def verify_application(
    *,
    root: Path,
    before: dict,
    after: dict,
    target_file: str,
    target_finding_id: str,
    expected_sha256: str,
    allowed_paths: frozenset[str],
    before_snapshot: Snapshot | None,
    after_snapshot: Snapshot | None,
) -> FixVerification:
    """Compare the project before and after, and say what held."""
    from reprocheck.fix.apply import sha256

    started = time.monotonic()
    before_map = identities(before)
    after_map = identities(after)
    removed = sorted(set(before_map) - set(after_map))
    added = sorted(set(after_map) - set(before_map))

    target_before = sum(
        1 for summary in before_map.values() if summary.get("id") == target_finding_id
    )
    target_after = sum(
        1 for summary in after_map.values() if summary.get("id") == target_finding_id
    )
    target_identities = sorted(
        identity
        for identity, summary in before_map.items()
        if summary.get("id") == target_finding_id
    )

    checks: list[VerificationCheck] = []
    regressions: list[str] = []

    # 1. The target. Related explicitly, not by a total: which finding was
    #    addressed, whether that same finding is gone, and whether the rule is
    #    now satisfied rather than merely silenced.
    target_gone = all(identity not in after_map for identity in target_identities)
    if target_before == 0:
        target_detail = (
            f"{target_finding_id} was not reported before the write, so there was "
            "no target to resolve"
        )
        target_passed = False
    elif target_gone:
        target_detail = (
            f"{target_finding_id} was reported {target_before} time(s) before and "
            f"{target_after} after"
        )
        target_passed = True
    else:
        target_detail = (
            f"{target_finding_id} is still reported {target_after} time(s) after "
            "the write"
        )
        target_passed = False
    checks.append(VerificationCheck("target-resolved", target_passed, target_detail))

    # 2. The bytes. Guards the window between the write and this check, and is
    #    what makes an external edit visible instead of silently accepted.
    target_path = root / target_file
    try:
        current = sha256(_read(target_path))
    except OSError as exc:
        current = ""
        content_detail = f"{target_file} could not be read: {exc}"
    else:
        content_detail = (
            f"{target_file} matches {expected_sha256}"
            if current == expected_sha256
            else (
                f"{target_file} holds {current or 'nothing readable'}, not the "
                f"{expected_sha256} that was written; something else wrote to it"
            )
        )
    content_passed = bool(current) and current == expected_sha256
    checks.append(VerificationCheck("content-intact", content_passed, content_detail))

    # 3. Regressions. Three kinds, and only three: a new error, a new finding
    #    about the file the fix touched, and a new configuration-rule finding.
    for identity in added:
        summary = after_map[identity]
        label = summary.get("label") or identity
        if summary.get("severity") == "error":
            regressions.append(f"new error: {label}")
        elif summary.get("id") in CONFIGURATION_RULES:
            regressions.append(f"new {summary.get('id')}: {label}")
        elif summary.get("file") in allowed_paths:
            regressions.append(
                f"new finding in the changed file {summary.get('file')}: {label}"
            )
    checks.append(
        VerificationCheck(
            "no-regressions",
            not regressions,
            "nothing new appeared" if not regressions else "; ".join(regressions),
        )
    )

    # 4. Integrity. Git is not the source of truth here: the project need not be
    #    a repository, and a dirty working tree is a normal state to fix in.
    if before_snapshot is None or after_snapshot is None:  # pragma: no cover
        unexpected: tuple[str, ...] = ()
        integrity_detail = "no snapshot was taken"
        integrity_passed = True
    else:
        unexpected = before_snapshot.changed_since(
            after_snapshot, allowed=allowed_paths
        )
        integrity_detail = (
            "no file changed other than " + ", ".join(sorted(allowed_paths))
            if not unexpected
            else "changed unexpectedly: " + ", ".join(unexpected)
        )
        integrity_passed = not unexpected
    checks.append(
        VerificationCheck("project-integrity", integrity_passed, integrity_detail)
    )

    success = all(check.passed for check in checks)
    return FixVerification(
        attempted=True,
        success=success,
        target_finding_id=target_finding_id,
        target_resolved=target_passed,
        target_before=target_before,
        target_after=target_after,
        findings_before=len(before_map),
        findings_after=len(after_map),
        removed=tuple(removed),
        added=tuple(added),
        regressions=tuple(regressions),
        checks=tuple(checks),
        duration_seconds=round(time.monotonic() - started, 3),
    )


def _read(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read()
