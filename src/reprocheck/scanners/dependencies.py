"""Static extraction of declared dependencies.

Every declaration is read from text only: no package is resolved, downloaded or
installed, and no URL is ever accessed. ``-r``/``-c`` includes are followed only
when the target is an existing local file inside the scanned project.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from reprocheck.facts import (
    DEP_SOURCE_BUILD_SYSTEM,
    DEP_SOURCE_CONSTRAINT,
    DEP_SOURCE_DEPENDENCY_GROUP,
    DEP_SOURCE_POETRY,
    DEP_SOURCE_PYPROJECT,
    DEP_SOURCE_REQUIREMENTS,
    KIND_BUILD,
    KIND_CONSTRAINT,
    KIND_DEV,
    KIND_OPTIONAL,
    KIND_RUNTIME,
    KIND_TEST,
    REF_LOCAL_PATH,
    REF_URL,
    REF_VCS,
    DependencyDeclaration,
    RequirementsInclude,
)

# Group names that describe a test-only environment.
_TEST_GROUP_HINTS = ("test", "testing")

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{7,40}$")
_VCS_PREFIXES = ("git+", "hg+", "svn+", "bzr+")
_CARET_RE = re.compile(r"^\^(\d+(?:\.\d+)*)")
_TILDE_RE = re.compile(r"^~(\d+(?:\.\d+)*)")
_EDITABLE_RE = re.compile(r"^(?:-e|--editable)\s+(?P<value>.+)$", re.IGNORECASE)
_REQUIREMENT_FILE_RE = re.compile(
    r"^(?P<flag>-r|--requirement|-c|--constraint)\s+(?P<value>.+)$", re.IGNORECASE
)
_ARCHIVE_SUFFIXES = (".whl", ".tar.gz", ".zip", ".tar.bz2", ".tar.xz")


@dataclass(frozen=True, slots=True)
class DependencyScan:
    """Everything the dependency scanner observed."""

    declarations: list[DependencyDeclaration]
    includes: list[RequirementsInclude]

    @property
    def summary(self) -> dict[str, int]:
        counts = {
            KIND_RUNTIME: 0,
            KIND_DEV: 0,
            KIND_TEST: 0,
            KIND_OPTIONAL: 0,
            KIND_BUILD: 0,
            KIND_CONSTRAINT: 0,
        }
        for declaration in self.declarations:
            counts[declaration.kind] = counts.get(declaration.kind, 0) + 1
        counts["unique_packages"] = len(
            {item.name for item in self.declarations if item.name}
        )
        return counts


def scan_dependencies(root: Path) -> DependencyScan:
    """Extract every dependency declaration found in ``root``."""
    declarations: list[DependencyDeclaration] = []
    includes: list[RequirementsInclude] = []

    declarations.extend(_from_pyproject(root / "pyproject.toml"))
    declarations.extend(_from_requirements(root, includes))

    return DependencyScan(declarations=declarations, includes=includes)


# --------------------------------------------------------------------------- #
# pyproject.toml
# --------------------------------------------------------------------------- #


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _load_toml(path: Path) -> dict[str, object] | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return None


def _from_pyproject(path: Path) -> list[DependencyDeclaration]:
    data = _load_toml(path)
    if data is None:
        return []
    found: list[DependencyDeclaration] = []
    found.extend(_project_dependencies(data))
    found.extend(_optional_dependencies(data))
    found.extend(_dependency_groups(data))
    found.extend(_build_system(data))
    found.extend(_poetry(data))
    return found


def _project_dependencies(data: dict[str, object]) -> list[DependencyDeclaration]:
    project = data.get("project")
    if not isinstance(project, dict):
        return []
    return _parse_list(
        project.get("dependencies"),
        source=DEP_SOURCE_PYPROJECT,
        group=KIND_RUNTIME,
        kind=KIND_RUNTIME,
        file="pyproject.toml",
    )


def _optional_dependencies(data: dict[str, object]) -> list[DependencyDeclaration]:
    project = data.get("project")
    optional = (
        project.get("optional-dependencies") if isinstance(project, dict) else None
    )
    if not isinstance(optional, dict):
        return []
    found: list[DependencyDeclaration] = []
    for extra in sorted(optional):
        found.extend(
            _parse_list(
                optional[extra],
                source=DEP_SOURCE_PYPROJECT,
                group=str(extra),
                kind=KIND_OPTIONAL,
                file="pyproject.toml",
            )
        )
    return found


def _dependency_groups(data: dict[str, object]) -> list[DependencyDeclaration]:
    groups = data.get("dependency-groups")
    if not isinstance(groups, dict):
        return []
    found: list[DependencyDeclaration] = []
    for name in sorted(groups):
        kind = KIND_TEST if _looks_like_test_group(str(name)) else KIND_DEV
        found.extend(
            _parse_list(
                groups[name],
                source=DEP_SOURCE_DEPENDENCY_GROUP,
                group=str(name),
                kind=kind,
                file="pyproject.toml",
            )
        )
    return found


def _build_system(data: dict[str, object]) -> list[DependencyDeclaration]:
    build_system = data.get("build-system")
    if not isinstance(build_system, dict):
        return []
    return _parse_list(
        build_system.get("requires"),
        source=DEP_SOURCE_BUILD_SYSTEM,
        group=KIND_BUILD,
        kind=KIND_BUILD,
        file="pyproject.toml",
    )


def _poetry(data: dict[str, object]) -> list[DependencyDeclaration]:
    tool = data.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    if not isinstance(poetry, dict):
        return []

    found: list[DependencyDeclaration] = []
    main = poetry.get("dependencies")
    if isinstance(main, dict):
        found.extend(
            _parse_mapping(
                main,
                source=DEP_SOURCE_POETRY,
                group=KIND_RUNTIME,
                kind=KIND_RUNTIME,
                file="pyproject.toml",
                skip={"python"},
            )
        )
    groups = poetry.get("group")
    if isinstance(groups, dict):
        for name in sorted(groups):
            body = groups[name]
            dependencies = body.get("dependencies") if isinstance(body, dict) else None
            if not isinstance(dependencies, dict):
                continue
            found.extend(
                _parse_mapping(
                    dependencies,
                    source=DEP_SOURCE_POETRY,
                    group=str(name),
                    kind=KIND_DEV,
                    file="pyproject.toml",
                )
            )
    legacy = poetry.get("dev-dependencies")
    if isinstance(legacy, dict):
        found.extend(
            _parse_mapping(
                legacy,
                source=DEP_SOURCE_POETRY,
                group="dev",
                kind=KIND_DEV,
                file="pyproject.toml",
            )
        )
    return found


def _looks_like_test_group(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _TEST_GROUP_HINTS)


# --------------------------------------------------------------------------- #
# Generic PEP 508 parsing
# --------------------------------------------------------------------------- #


def _parse_list(
    values: object,
    *,
    source: str,
    group: str,
    kind: str,
    file: str | None,
) -> list[DependencyDeclaration]:
    if not isinstance(values, list):
        return []
    found: list[DependencyDeclaration] = []
    for value in values:
        if isinstance(value, str):
            declaration = _parse_requirement(
                value, source=source, group=group, kind=kind, file=file
            )
            if declaration is not None:
                found.append(declaration)
    return found


def _parse_mapping(
    values: dict[str, object],
    *,
    source: str,
    group: str,
    kind: str,
    file: str | None,
    skip: set[str] | None = None,
) -> list[DependencyDeclaration]:
    found: list[DependencyDeclaration] = []
    for name in sorted(values):
        if skip and name.lower() in skip:
            continue
        value = values[name]
        if isinstance(value, str):
            text = name + _poetry_constraint(value)
        elif isinstance(value, list):
            text = name + _poetry_constraint(",".join(str(item) for item in value))
        elif isinstance(value, dict):
            text = _poetry_table_to_requirement(name, value)
        else:
            continue
        declaration = _parse_requirement(
            text, source=source, group=group, kind=kind, file=file
        )
        if declaration is not None:
            found.append(declaration)
    return found


def _poetry_table_to_requirement(name: str, table: dict[str, object]) -> str:
    """Convert a Poetry dependency table into a PEP 508 requirement string."""
    text = name
    extras = table.get("extras")
    if isinstance(extras, list) and extras:
        text += "[" + ",".join(str(item) for item in extras) + "]"
    version = table.get("version")
    if isinstance(version, str) and version:
        text += _poetry_constraint(version)
    markers: list[str] = []
    for key in ("python", "sys_platform", "platform_system", "implementation_name"):
        value = table.get(key)
        if isinstance(value, str) and value:
            markers.append(f"{key} {value}")
    if markers:
        text += " ; " + " and ".join(markers)
    return text


def _poetry_constraint(value: str) -> str:
    """Translate a Poetry version constraint into PEP 440 syntax.

    Poetry's ``^`` and ``~`` are not PEP 440, so they are expanded here rather
    than rejected. ``*`` and ``>=0`` mean "any version".
    """
    text = value.strip()
    if text in {"*", ">=0", ""}:
        return ""
    caret = _CARET_RE.match(text)
    if caret:
        lower, upper = _poetry_range(caret.group(1), caret=True)
        return f">={lower},<{upper}"
    tilde = _TILDE_RE.match(text)
    if tilde:
        lower, upper = _poetry_range(tilde.group(1), caret=False)
        return f">={lower},<{upper}"
    if re.fullmatch(r"[\d.*]+", text):
        # A bare version is an exact requirement in Poetry.
        return "==" + text if "*" not in text else ""
    if text.startswith((">=", "<=", ">", "<", "==", "!=")):
        return text
    if re.fullmatch(r"[\d.]+", text):
        return "==" + text
    return text


def _poetry_range(version: str, *, caret: bool) -> tuple[str, str]:
    parts = [int(part) for part in version.split(".")]
    while len(parts) < 3:
        parts.append(0)
    lower = ".".join(str(part) for part in parts)
    if caret:
        if parts[0] > 0:
            upper = [parts[0] + 1, 0, 0]
        elif parts[1] > 0:
            upper = [0, parts[1] + 1, 0]
        else:
            upper = [0, 0, parts[2] + 1]
    else:
        if len(version.split(".")) >= 2:
            upper = [parts[0], parts[1] + 1, 0]
        else:
            upper = [parts[0] + 1, 0, 0]
    return (lower, ".".join(str(part) for part in upper))


def _parse_requirement(
    value: str,
    *,
    source: str,
    group: str,
    kind: str,
    file: str | None,
    line: int | None = None,
) -> DependencyDeclaration | None:
    raw = value.strip()
    if not raw:
        return None
    try:
        requirement = Requirement(raw)
    except InvalidRequirement:
        return _parse_non_pep508(raw, source=source, group=group, kind=kind, file=file)

    reference = requirement.url
    reference_kind = _reference_kind(reference)
    vcs_ref, vcs_commit = _vcs_details(reference)
    return DependencyDeclaration(
        name=_normalise(requirement.name),
        raw_name=requirement.name,
        specifier=str(requirement.specifier),
        source=source,
        group=group,
        kind=kind,
        marker=str(requirement.marker) if requirement.marker else None,
        extras=tuple(sorted(requirement.extras)),
        file=file,
        line=line,
        raw=raw,
        reference=reference,
        reference_kind=reference_kind,
        vcs_ref=vcs_ref,
        vcs_commit=vcs_commit,
    )


def _parse_non_pep508(
    raw: str,
    *,
    source: str,
    group: str,
    kind: str,
    file: str | None,
    line: int | None = None,
) -> DependencyDeclaration | None:
    """Handle bare VCS URLs and local paths, which are not PEP 508."""
    if raw.lower().startswith(_VCS_PREFIXES) or _looks_like_url(raw):
        name = _egg_name(raw)
        return DependencyDeclaration(
            name=_normalise(name) if name else "",
            raw_name=name or raw,
            specifier="",
            source=source,
            group=group,
            kind=kind,
            file=file,
            line=line,
            raw=raw,
            reference=raw,
            reference_kind=REF_VCS
            if raw.lower().startswith(_VCS_PREFIXES)
            else REF_URL,
            vcs_ref=_vcs_details(raw)[0],
            vcs_commit=_vcs_details(raw)[1],
        )
    if raw.lower().startswith(("file:", ".", "/", "\\")) or _WINDOWS_PATH_RE.match(raw):
        return DependencyDeclaration(
            name="",
            raw_name=raw,
            specifier="",
            source=source,
            group=group,
            kind=kind,
            file=file,
            line=line,
            raw=raw,
            reference=raw,
            reference_kind=REF_LOCAL_PATH,
        )
    return None


_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")

# A ``#egg=`` fragment names the distribution for bare URLs.
_EGG_RE = re.compile(r"#egg=([A-Za-z0-9._-]+)")


def _egg_name(value: str) -> str | None:
    match = _EGG_RE.search(value)
    return match.group(1) if match else None


def _normalise(name: str) -> str:
    return str(canonicalize_name(name))


def _looks_like_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith(("http://", "https://", "ftp://")) or lowered.endswith(
        _ARCHIVE_SUFFIXES
    )


def _reference_kind(reference: str | None) -> str | None:
    if not reference:
        return None
    lowered = reference.lower()
    if lowered.startswith(_VCS_PREFIXES):
        return REF_VCS
    if lowered.startswith("file:"):
        return REF_LOCAL_PATH
    if _looks_like_url(reference):
        return REF_URL
    return None


def _vcs_details(reference: str | None) -> tuple[str | None, str | None]:
    """Return the VCS ref and the commit hash, when the URL carries one."""
    if not reference:
        return (None, None)
    match = re.search(r"@(?P<ref>[^@/]+?)(?=$|[#?])", reference)
    if not match:
        return (None, None)
    ref = match.group("ref")
    if _COMMIT_RE.match(ref):
        return (ref, ref)
    return (ref, None)


# --------------------------------------------------------------------------- #
# requirements*.txt
# --------------------------------------------------------------------------- #


def _from_requirements(
    root: Path, includes: list[RequirementsInclude]
) -> list[DependencyDeclaration]:
    declarations: list[DependencyDeclaration] = []
    # One shared visited set: a file is parsed at most once, which also makes
    # include cycles terminate.
    visited: set[str] = set()
    for path in sorted(_requirement_files(root)):
        relative = path.relative_to(root).as_posix()
        declarations.extend(
            _parse_requirements_file(root, path, relative, includes, visited)
        )
    return declarations


def _requirement_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.glob("requirements*.txt")):
        if path.is_file():
            files.append(path)
    for path in sorted(root.glob("requirements/*.txt")):
        if path.is_file():
            files.append(path)
    return files


def _parse_requirements_file(
    root: Path,
    path: Path,
    relative: str,
    includes: list[RequirementsInclude],
    visited: set[str],
) -> list[DependencyDeclaration]:
    if relative in visited:
        return []
    visited.add(relative)
    text = _read_text(path)
    if text is None:
        return []

    declarations: list[DependencyDeclaration] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw).strip()
        if not line:
            continue

        include = _REQUIREMENT_FILE_RE.match(line)
        if include:
            includes.append(
                _resolve_include(
                    root,
                    path,
                    relative,
                    number,
                    include.group("value").strip(),
                    "constraint"
                    if include.group("flag").lower() in {"-c", "--constraint"}
                    else "include",
                )
            )
            continue

        editable = _EDITABLE_RE.match(line)
        if editable:
            declarations.append(
                _local_path_declaration(
                    editable.group("value").strip(),
                    source=DEP_SOURCE_REQUIREMENTS,
                    group=KIND_DEV,
                    kind=KIND_DEV,
                    file=relative,
                    line=number,
                    raw=line,
                )
            )
            continue

        kind = _requirements_kind(relative)
        declaration = _parse_requirement(
            line,
            source=DEP_SOURCE_CONSTRAINT
            if kind == KIND_CONSTRAINT
            else DEP_SOURCE_REQUIREMENTS,
            group=_requirements_group(relative),
            kind=kind,
            file=relative,
            line=number,
        )
        if declaration is not None:
            declarations.append(declaration)

    declarations.extend(_follow_includes(root, path, relative, includes, visited))
    return declarations


def _strip_comment(line: str) -> str:
    """Remove a trailing comment that is not inside a URL fragment."""
    if "#" not in line:
        return line
    head, _, tail = line.partition("#")
    if "egg=" in tail:
        return line
    return head


def _requirements_group(relative: str) -> str:
    return "constraints" if _is_constraint_file(relative) else relative


def _requirements_kind(relative: str) -> str:
    if _is_constraint_file(relative):
        return KIND_CONSTRAINT
    lowered = relative.lower()
    if _looks_like_test_group(lowered):
        return KIND_TEST
    if "dev" in lowered or "lint" in lowered or "docs" in lowered:
        return KIND_DEV
    return KIND_RUNTIME


def _is_constraint_file(relative: str) -> bool:
    return Path(relative).name.lower().startswith("constraint")


def _resolve_include(
    root: Path,
    source: Path,
    relative: str,
    line: int,
    value: str,
    include_kind: str,
) -> RequirementsInclude:
    target = value.split("#", 1)[0].strip()
    if _looks_like_url(target) or target.lower().startswith(_VCS_PREFIXES):
        return RequirementsInclude(
            include_kind=include_kind,
            value=value,
            file=relative,
            line=line,
            skipped_reason="remote-include",
        )
    candidate = (source.parent / target).resolve()
    try:
        inside = candidate.is_relative_to(root.resolve())
    except (OSError, ValueError):  # pragma: no cover - defensive
        inside = False
    if not inside:
        return RequirementsInclude(
            include_kind=include_kind,
            value=value,
            file=relative,
            line=line,
            skipped_reason="outside-project",
        )
    resolved = candidate.relative_to(root.resolve()).as_posix()
    exists = candidate.is_file()
    return RequirementsInclude(
        include_kind=include_kind,
        value=value,
        file=relative,
        line=line,
        resolved=resolved,
        exists=exists,
        skipped_reason=None if exists else "missing",
    )


def _follow_includes(
    root: Path,
    path: Path,
    relative: str,
    includes: list[RequirementsInclude],
    visited: set[str],
) -> list[DependencyDeclaration]:
    declarations: list[DependencyDeclaration] = []
    for include in includes:
        if include.file != relative or include.resolved is None or not include.exists:
            continue
        if include.resolved in visited:
            continue
        target = root / include.resolved
        declarations.extend(
            _parse_requirements_file(root, target, include.resolved, includes, visited)
        )
    return declarations


def _local_path_declaration(
    value: str,
    *,
    source: str,
    group: str,
    kind: str,
    file: str,
    line: int | None,
    raw: str,
) -> DependencyDeclaration:
    path, _, fragment = value.partition("#")
    name_match = _EGG_RE.search(f"#{fragment}" if fragment else "")
    return DependencyDeclaration(
        name=_normalise(name_match.group(1)) if name_match else "",
        raw_name=name_match.group(1) if name_match else path.strip(),
        source=source,
        group=group,
        kind=kind,
        file=file,
        line=line,
        raw=raw,
        reference=path.strip(),
        reference_kind=REF_LOCAL_PATH,
    )
