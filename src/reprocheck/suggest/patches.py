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

    When the file uses CRLF, the carriage return is written back into the body
    lines, exactly as ``git diff`` does with ``core.autocrlf`` disabled. A patch
    that dropped it would still be *accepted* by ``git apply`` on a CRLF file and
    would silently rewrite it with LF endings, which is not a change anybody
    reviewed. The headers and the marker never carry the carriage return.
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
    lines = _fix_counts(_restore_markers(lines))
    if "\r\n" in before or "\r\n" in after:
        lines = _restore_carriage_returns(lines)
    return "\n".join(lines)


def _restore_carriage_returns(lines: list[str]) -> list[str]:
    """Put the CR of a CRLF file back into the hunk body only."""
    result: list[str] = []
    in_hunk = False
    for line in lines:
        if line.startswith("@@"):
            in_hunk = True
            result.append(line)
            continue
        if in_hunk and line[:1] in {" ", "+", "-"}:
            result.append(line + "\r")
            continue
        result.append(line)
    return result


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


def _fix_counts(lines: list[str]) -> list[str]:
    """Recompute the line counts of every hunk from its body.

    Turning a sentinel into a ``-``/``+`` pair changes the real line counts, and
    a header that disagrees with its body is a corrupt patch for ``git apply``.
    """
    result: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("@@"):
            result.append(line)
            index += 1
            continue
        end = index + 1
        before = after = 0
        while end < len(lines) and not lines[end].startswith("@@"):
            marker = lines[end][:1]
            if marker in {" ", "-"}:
                before += 1
            if marker in {" ", "+"}:
                after += 1
            end += 1
        parts = line.split(" ")
        before_start = parts[1].split(",")[0]
        after_start = parts[2].split(",")[0]
        result.append(f"@@ {before_start},{before} {after_start},{after} @@")
        result.extend(lines[index + 1 : end])
        index = end
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
