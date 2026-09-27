"""Collection of declared Python version requirements.

Values from different sources are recorded verbatim; V0.1 performs no
reconciliation and no conflict resolution.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from reprocheck.models import PythonRequirement
from reprocheck.scanners.files import scan_workflow_files

# ``python-version: "3.11"`` / ``python_version = 3.11`` style declarations.
_VERSION_RE = re.compile(
    r"""python[-_ ]?versions?\s*[:=]\s*(?P<value>.+)$""", re.IGNORECASE
)
# Fallback used when pyproject.toml cannot be parsed as TOML.
_REQUIRES_PYTHON_RE = re.compile(
    r"""["']?requires-python["']?\s*=\s*(?P<value>[^\n#]+)""", re.IGNORECASE
)
_MATRIX_EXPR_RE = re.compile(r"^\$\{\{\s*matrix\.(?P<key>[^}]+?)\s*\}\}$")
_MATRIX_LIST_RE = re.compile(r"^\s*{key}\s*:\s*\[(?P<values>[^\]]*)\]\s*$")
_COMMENT_RE = re.compile(r"\s+#.*$")


def scan_python_requirements(root: Path) -> list[PythonRequirement]:
    """Collect every Python version requirement found in ``root``."""
    requirements: list[PythonRequirement] = []
    requirements.extend(_from_python_version_file(root / ".python-version"))
    requirements.extend(_from_pyproject(root / "pyproject.toml"))
    requirements.extend(_from_workflows(root))
    return requirements


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _from_python_version_file(path: Path) -> list[PythonRequirement]:
    if not path.is_file():
        return []
    text = _read_text(path)
    if text is None:
        return []
    found: list[PythonRequirement] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        found.append(
            PythonRequirement(
                source=".python-version",
                value=value,
                file=".python-version",
                line=number,
            )
        )
    return found


def _from_pyproject(path: Path) -> list[PythonRequirement]:
    if not path.is_file():
        return []
    text = _read_text(path)
    if text is None:
        return []
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return _from_pyproject_regex(text)

    found: list[PythonRequirement] = []
    project = data.get("project")
    if isinstance(project, dict):
        value = project.get("requires-python")
        if isinstance(value, str) and value.strip():
            found.append(
                PythonRequirement(
                    source="pyproject.toml [project.requires-python]",
                    value=value.strip(),
                    file="pyproject.toml",
                )
            )
    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            dependencies = poetry.get("dependencies")
            if isinstance(dependencies, dict):
                value = dependencies.get("python")
                if isinstance(value, str) and value.strip():
                    found.append(
                        PythonRequirement(
                            source="pyproject.toml [tool.poetry.dependencies.python]",
                            value=value.strip(),
                            file="pyproject.toml",
                        )
                    )
    return found


def _from_pyproject_regex(text: str) -> list[PythonRequirement]:
    found: list[PythonRequirement] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        match = _REQUIRES_PYTHON_RE.search(raw)
        if match:
            found.append(
                PythonRequirement(
                    source="pyproject.toml [requires-python]",
                    value=match.group("value").strip().strip("\"'"),
                    file="pyproject.toml",
                    line=number,
                )
            )
    return found


def _from_workflows(root: Path) -> list[PythonRequirement]:
    found: list[PythonRequirement] = []
    for relative in scan_workflow_files(root):
        text = _read_text(root / relative)
        if text is None:
            continue
        found.extend(_from_workflow_text(relative, text))
    return found


def _from_workflow_text(relative: str, text: str) -> list[PythonRequirement]:
    found: list[PythonRequirement] = []
    lines = text.splitlines()
    for index, raw in enumerate(lines):
        match = _VERSION_RE.search(_COMMENT_RE.sub("", raw))
        if not match:
            continue
        value = _clean_value(match.group("value"))
        if not value:
            continue
        number = index + 1
        matrix = _MATRIX_EXPR_RE.match(value)
        if matrix:
            found.extend(_resolve_matrix(relative, lines, matrix.group("key"), number))
            continue
        if value.startswith("["):
            if _inside_matrix_block(lines, index):
                # A ``python-version:`` list under ``matrix:`` only defines the
                # matrix; its values are reported where the matrix is used.
                continue
            values = _split_inline_list(value)
        else:
            values = [value]
        found.extend(
            PythonRequirement(
                source=f"{relative} [python-version]",
                value=item,
                file=relative,
                line=number,
            )
            for item in values
        )
    return found


def _resolve_matrix(
    relative: str, lines: list[str], key: str, line: int
) -> list[PythonRequirement]:
    pattern = _MATRIX_LIST_RE.pattern.format(key=re.escape(key))
    for index, raw in enumerate(lines):
        match = re.match(pattern, raw)
        if not match or not _inside_matrix_block(lines, index):
            continue
        values = [_clean_value(part) for part in match.group("values").split(",")]
        return [
            PythonRequirement(
                source=f"{relative} [matrix.{key}]",
                value=value,
                file=relative,
                line=line,
            )
            for value in values
            if value
        ]
    return [
        PythonRequirement(
            source=f"{relative} [matrix.{key}]",
            value=f"${{{{ matrix.{key} }}}}",
            file=relative,
            line=line,
        )
    ]


def _inside_matrix_block(lines: list[str], index: int) -> bool:
    """Return ``True`` when ``lines[index]`` is nested under a ``matrix:`` key."""
    indent = _indent_of(lines[index])
    for previous in reversed(lines[:index]):
        stripped = previous.strip()
        if not stripped or stripped.startswith("#"):
            continue
        previous_indent = _indent_of(previous)
        if previous_indent < indent and stripped.rstrip(":").strip() == "matrix":
            return True
        if previous_indent == 0:
            return False
    return False


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _split_inline_list(value: str) -> list[str]:
    inner = value[1:-1] if value.endswith("]") else value[1:]
    return [
        cleaned
        for cleaned in (_clean_value(part) for part in inner.split(","))
        if cleaned
    ]


def _clean_value(value: str) -> str:
    return value.strip().strip("\"'").strip()
