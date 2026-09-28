"""In-memory unified diffs.

Nothing here writes a file. A proposal is built from the current content of a
file and the content it would have, both as strings in memory; the project is
only ever read.
"""

from __future__ import annotations

import difflib

#: Default number of context lines, the same as ``git diff``.
CONTEXT_LINES = 3

#: The marker ``git`` uses for a file that does not end with a newline.
NO_NEWLINE_MARKER = "\\ No newline at end of file"

_SENTINEL = "\x00reprocheck:no-final-newline"


def detect_newline(content: str) -> str:
    """Return the line ending used by ``content``.

    ``\\n`` is returned for a file with no line ending at all, so a single-line
    file is never rewritten into a CRLF file by accident.
    """
    return "\r\n" if "\r\n" in content else "\n"


def has_final_newline(content: str) -> bool:
    return content.endswith(("\n", "\r"))


def unified(
    before: str,
    after: str,
    path: str,
    *,
    context: int = CONTEXT_LINES,
) -> str:
    """Return a standard unified diff of ``before`` → ``after`` for ``path``.

    ``difflib`` compares lines without their endings, which makes a missing
    final newline invisible. A diff that hides it would produce a broken patch,
    so a sentinel line is added on each side that lacks one and replaced by the
    conventional ``\\ No newline at end of file`` marker afterwards.
    """
    before_lines = _with_sentinel(before)
    after_lines = _with_sentinel(after)
    lines = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
            n=context,
        )
    )
    return "\n".join(_restore_markers(lines))


def _with_sentinel(content: str) -> list[str]:
    lines = content.splitlines()
    if content and not has_final_newline(content):
        lines.append(_SENTINEL)
    return lines


def _restore_markers(lines: list[str]) -> list[str]:
    result: list[str] = []
    for line in lines:
        if not line.endswith(_SENTINEL):
            result.append(line)
            continue
        # The line before the sentinel is the last line without a newline: state
        # it as a change, exactly as git does.
        previous = result.pop() if result else " "
        text = previous[1:]
        result.append(f"-{text}")
        result.append(NO_NEWLINE_MARKER)
        result.append(f"+{text}")
    return result


def append_lines(content: str, lines: list[str], *, newline: str | None = None) -> str:
    """Append ``lines`` to ``content``, preserving its line endings.

    The existing content is never reformatted and no existing line is touched.
    A file without a final newline gets one, because otherwise the first
    appended pattern would be glued to the previous line.
    """
    ending = newline or detect_newline(content)
    body = content
    if body and not has_final_newline(body):
        body += ending
    for line in lines:
        body = f"{body}{line}{ending}"
    return body
