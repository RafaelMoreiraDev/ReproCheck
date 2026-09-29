"""Read Conda environment declarations, statically and read-only.

What this module does and, just as importantly, what it does not do:

* it **reads** ``environment.yml`` / ``environment.yaml`` and nothing else. No
  conda process is started, no channel is contacted, no lockfile is resolved
  and no environment is created;
* YAML is parsed with the safe loader only, so a custom tag can never construct
  an arbitrary Python object. A file that uses one is reported as unsupported
  rather than executed;
* a file is treated as a Conda environment because of its **name**, or because
  the project explicitly names it. No YAML is guessed to be an environment just
  because it parses;
* Conda version syntax is preserved verbatim. A PEP 440 translation is produced
  only where the two accept the same set of versions, so a downstream check can
  never prove a conflict Conda itself would not have.

Two details drove this design.

``numpy=1.26`` is **not** ``numpy==1.26``. Conda's single ``=`` pins the
components that are written and lets the rest float, so it is the same set as
``==1.26.*``; the two were checked against :mod:`packaging`, not assumed.

``numpy  # [win]`` is a **YAML comment**, so ``yaml.safe_load`` throws it away.
Selectors are therefore recovered from the raw text and attached by line number,
which is also where the line numbers in the facts come from: the node tree is
walked with :func:`yaml.compose`, which reports positions but constructs no
Python object.
"""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name

from reprocheck.facts import (
    CONDA_ENVIRONMENT_FILENAMES,
    CONDA_ENVIRONMENT_PREFIXES,
    CONDA_SOURCE_DEPENDENCY,
    CONDA_SOURCE_PIP,
    DEP_SOURCE_CONDA_PIP,
    KIND_RUNTIME,
    CondaDependency,
    CondaEnvironment,
)

#: A trailing ``# [win]`` comment: Conda's platform selector syntax.
_SELECTOR_RE = re.compile(r"#\s*\[([^\]]*)\]\s*$")

#: ``channel::name`` — an explicit channel for one package.
_CHANNEL_RE = re.compile(r"^(?P<channel>[A-Za-z0-9._-]+)::(?P<rest>.+)$")

#: One version clause inside a Conda specifier.
_CLAUSE_RE = re.compile(
    r"^(?P<operator>==|>=|<=|!=|~=|<|>|=)?\s*(?P<version>[0-9][^\s,=<>!~]*)$"
)

#: Selectors ReproCheck records. It does not evaluate them: a conditional
#: dependency is reported with its condition so no check can treat it as
#: universal, which is what would invent a cross-platform conflict.
SELECTOR_ALIASES = {
    "win": "win",
    "win32": "win",
    "win-32": "win",
    "win-64": "win",
    "linux": "linux",
    "linux-64": "linux",
    "osx": "osx",
    "unix": "osx",
    "darwin": "osx",
}

#: Top-level keys ReproCheck interprets. Anything else is recorded and ignored.
_KNOWN_KEYS = frozenset({"name", "channels", "dependencies", "prefix", "variables"})


@dataclass(frozen=True, slots=True)
class CondaScan:
    """Everything the Conda scanner observed."""

    environments: list[CondaEnvironment] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "environments": len(self.environments),
            "dependencies": sum(
                len(item.conda_dependencies) for item in self.environments
            ),
            "pip_dependencies": sum(
                len(item.pip_dependencies) for item in self.environments
            ),
            "channels": len(
                {channel for item in self.environments for channel in item.channels}
            ),
        }


# --------------------------------------------------------------------------- #
# File selection
# --------------------------------------------------------------------------- #


def candidate_files(root: Path, *, explicitly_named: frozenset[str]) -> list[Path]:
    """Return the environment files to read.

    ``environment.yml`` and ``environment.yaml`` at the root are taken as Conda
    environments because that is what the name means. Any other name beginning
    with ``environment-`` is only taken when the project **names** it. Guessing
    would make a finding depend on a filename coincidence.
    """
    found: list[Path] = []
    for name in CONDA_ENVIRONMENT_FILENAMES:
        path = root / name
        if path.is_file():
            found.append(path)
    for path in sorted(root.glob("environment*.y*ml")):
        if path.is_file() and path not in found:
            if path.name.startswith(CONDA_ENVIRONMENT_PREFIXES) and (
                path.name in explicitly_named
            ):
                found.append(path)
    return found


#: Files whose text is searched for an explicit mention of an extra environment.
#: README files and CI workflows are where a project documents how to create
#: its environment, so a name found there is a deliberate reference.
_MENTION_SOURCES = (
    "README.md",
    "README.rst",
    "CONTRIBUTING.md",
    "docs",
    ".github",
)


def explicitly_named_environment_files(root: Path) -> frozenset[str]:
    """Names of environment files the project points at, outside the root pair.

    The root ``environment.yml`` is always an environment, so it needs no
    mention. Every other candidate has to be named somewhere in the project's
    own documentation or CI before ReproCheck will read it: a YAML file that
    happens to sit in the root is not evidence that it is a Conda environment.
    """
    found: set[str] = set()
    for source in _MENTION_SOURCES:
        path = root / source
        if path.is_file():
            found |= _mentions_in(_read_text(path))
        elif path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix in {
                    ".md",
                    ".rst",
                    ".yml",
                    ".yaml",
                    ".toml",
                }:
                    found |= _mentions_in(_read_text(child))
    return frozenset(
        name for name in found if not name.startswith(CONDA_ENVIRONMENT_FILENAMES)
    )


def _mentions_in(text: str | None) -> set[str]:
    if not text:
        return set()
    return set(re.findall(r"\benvironment[-_][\w.-]*\.ya?ml\b", text))


def _read_text(path: Path) -> str | None:
    raw = _read_bytes(path)
    return None if raw is None else _decode(raw)


# --------------------------------------------------------------------------- #
# Version translation
# --------------------------------------------------------------------------- #


def conda_to_pep440(spec: str) -> str | None:
    """Translate a Conda version specifier to PEP 440, or return ``None``.

    ``None`` means "not safely comparable", and every check treats that as
    unknown rather than as agreement or as conflict.

    Conda's single ``=`` is a compatible-release pin on the components actually
    written: ``=1.26`` is the whole 1.26 series, and ``=1.26.4`` is the whole
    1.26.4 series. Both translate to ``==X.*``, which
    :class:`packaging.specifiers.SpecifierSet` accepts and which was verified to
    accept exactly the same versions. An entry that already carries its own
    ``*`` keeps it rather than growing a second one.
    """
    text = spec.strip()
    if not text:
        return ""
    parts: list[str] = []
    for clause in text.split(","):
        clause = clause.strip()
        if not clause:
            return None
        match = _CLAUSE_RE.match(clause)
        if match is None:
            return None
        operator = match.group("operator") or ""
        version = match.group("version")
        if not _is_version_like(version):
            return None
        if operator == "=":
            parts.append(f"=={version}" if version.endswith("*") else f"=={version}.*")
        elif operator == "":
            parts.append(f"=={version}")
        else:
            parts.append(f"{operator}{version}")
    candidate = ",".join(parts)
    try:
        SpecifierSet(candidate)
    except InvalidSpecifier:
        return None
    return candidate


def _is_version_like(value: str) -> bool:
    """A dotted numeric version, optionally closed by Conda's ``*``."""
    body = value[:-1] if value.endswith("*") else value
    return bool(body) and all(char.isdigit() or char == "." for char in body)


# --------------------------------------------------------------------------- #
# Entry parsing
# --------------------------------------------------------------------------- #


def parse_dependency(
    entry: str,
    *,
    file: str,
    line: int | None = None,
    source: str = CONDA_SOURCE_DEPENDENCY,
    selector: tuple[str, ...] = (),
) -> CondaDependency | None:
    """Turn one ``dependencies:`` entry into a :class:`CondaDependency`.

    ``selector`` is supplied by the caller because the selector is a YAML
    comment and is therefore not present in the loaded value.
    """
    raw = entry.strip()
    if not raw:
        return None

    channel: str | None = None
    channel_match = _CHANNEL_RE.match(raw)
    if channel_match is not None:
        channel = channel_match.group("channel")
        raw = channel_match.group("rest").strip()

    name, spec, build = _split_name_and_spec(raw)
    if not name:
        return None

    return CondaDependency(
        name=name,
        raw=entry.strip(),
        source=source,
        channel=channel,
        build=build,
        raw_spec=spec,
        pep440_specifier=conda_to_pep440(spec) if spec else "",
        selector=selector,
        file=file,
        line=line,
        is_python=canonicalize_name(name) == "python",
    )


def parse_pip_requirement(
    entry: str, *, file: str, line: int | None
) -> CondaDependency | None:
    """Turn one entry of the ``pip:`` subsection into a fact.

    These are PEP 508 strings, so they go through the same parser every
    requirements file uses. The origin is still recorded, so a report never
    presents a Conda file as a requirements file.
    """
    from reprocheck.scanners.dependencies import _parse_requirement

    declaration = _parse_requirement(
        entry.strip(),
        source=DEP_SOURCE_CONDA_PIP,
        group=KIND_RUNTIME,
        kind=KIND_RUNTIME,
        file=file,
        line=line,
    )
    if declaration is None:
        return None
    return CondaDependency(
        name=declaration.name,
        raw=entry.strip(),
        source=CONDA_SOURCE_PIP,
        raw_spec=declaration.specifier,
        pep440_specifier=declaration.specifier,
        selector=(),
        file=file,
        line=line,
        is_python=declaration.name == "python",
    )


def _split_name_and_spec(text: str) -> tuple[str, str, str | None]:
    """Split ``numpy=1.26=py311np123`` into name, spec and build string.

    The operator is kept in the returned spec. Dropping Conda's ``=`` would turn
    ``python=3.11`` into the exact pin ``==3.11``, which excludes 3.11.4 and
    would then be provably incompatible with a perfectly normal
    ``requires-python = ">=3.11"``: a false conflict invented by the parser.

    The build string is separated rather than parsed: ``py311np123`` is not a
    version, and :mod:`packaging` rejects it, which is exactly why it must not
    reach the specifier translation.
    """
    stripped = text.strip()
    if " " in stripped:
        name, _, rest = stripped.partition(" ")
        return name.strip(), f"={rest.strip()}" if rest.strip() else "", None

    for operator in ("==", ">=", "<=", "!=", "~=", "=", "<", ">"):
        index = stripped.find(operator)
        if index <= 0:
            continue
        name = stripped[:index]
        rest = stripped[index + len(operator) :]
        if operator == "=" and "=" in rest:
            spec, _, build = rest.partition("=")
            return name.strip(), f"={spec.strip()}", build.strip() or None
        return name.strip(), f"{operator}{rest.strip()}", None
    return stripped, "", None


# --------------------------------------------------------------------------- #
# Selector recovery
# --------------------------------------------------------------------------- #


def selectors_by_line(text: str) -> dict[int, tuple[str, ...]]:
    """Map 1-based line number to the platform selectors written on it.

    The selector is a YAML comment, so the loader discards it. Recovering it
    from the raw text is the only way to know that ``pyobjc`` is declared for
    macOS only, and that knowledge is what stops a cross-platform false
    conflict later.
    """
    found: dict[int, tuple[str, ...]] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        match = _SELECTOR_RE.search(line)
        if match is None:
            continue
        selectors = _selectors(match.group(1))
        if selectors:
            found[number] = selectors
    return found


def _selectors(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for token in re.split(r"[,\s]+", text.strip()):
        token = token.strip()
        if token:
            found.append(SELECTOR_ALIASES.get(token.lower(), token.lower()))
    return tuple(sorted(set(found)))


# --------------------------------------------------------------------------- #
# File parsing
# --------------------------------------------------------------------------- #


def parse_environment(path: Path, root: Path) -> CondaEnvironment:
    """Read one environment file into facts, never raising.

    Every failure mode becomes a value on the returned object: a byte-order
    mark, an undecodable file, malformed YAML, an unsupported custom tag and an
    unrecognised top-level structure are all reported, and none of them stops
    the scan.
    """
    relative = path.relative_to(root).as_posix()
    raw_bytes = _read_bytes(path)
    if raw_bytes is None:
        return CondaEnvironment(
            file=relative, parse_error="the file could not be read as bytes"
        )
    text = _decode(raw_bytes)
    if text is None:
        return CondaEnvironment(
            file=relative, parse_error="the file is not valid UTF-8 text"
        )

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        return CondaEnvironment(
            file=relative,
            parse_error=f"the YAML could not be parsed: {_short(error)}",
        )

    if document is None:
        return CondaEnvironment(file=relative, parse_error="the file is empty")
    if not isinstance(document, dict):
        return CondaEnvironment(
            file=relative,
            parse_error=(
                f"the top level is {type(document).__name__}, not a mapping of "
                "environment keys"
            ),
        )

    selectors = selectors_by_line(text)
    unsupported = [
        f"top-level key '{key}' is not interpreted"
        for key in sorted(str(item) for item in document)
        if key not in _KNOWN_KEYS
    ]
    dependencies, issues = _dependencies(
        document.get("dependencies"), relative, text, selectors
    )
    unsupported.extend(issues)

    name = document.get("name")
    return CondaEnvironment(
        file=relative,
        name=str(name) if name is not None else None,
        channels=_string_list(document.get("channels")),
        variables=tuple(
            sorted(
                f"{key}={value}"
                for key, value in _mapping(document.get("variables")).items()
            )
        ),
        dependencies=dependencies,
        unsupported=tuple(unsupported),
    )


def _dependency_lines(text: str) -> dict[int, int]:
    """Map the ordinal of a ``dependencies:`` entry to its 1-based line.

    Walked with :func:`yaml.compose`, which reports positions and constructs no
    Python object, so a custom tag still cannot be instantiated.
    """
    try:
        node = yaml.compose(text)
    except yaml.YAMLError:
        return {}
    if node is None or not isinstance(node, yaml.MappingNode):
        return {}
    for key_node, value_node in node.value:
        if getattr(key_node, "value", None) != "dependencies":
            continue
        if not isinstance(value_node, yaml.SequenceNode):
            return {}
        return {
            index: item.start_mark.line + 1
            for index, item in enumerate(value_node.value)
        }
    return {}


def _dependencies(
    value: object,
    relative: str,
    text: str,
    selectors: dict[int, tuple[str, ...]],
) -> tuple[tuple[CondaDependency, ...], list[str]]:
    """Read the ``dependencies:`` list, or report why it could not be read."""
    if value is None:
        return (), []
    if not isinstance(value, list):
        return (), ["'dependencies' is not a list"]

    positions = _dependency_lines(text)
    found: list[CondaDependency] = []
    issues: list[str] = []
    for index, item in enumerate(value):
        line = positions.get(index)
        if isinstance(item, str):
            dependency = parse_dependency(
                item,
                file=relative,
                line=line,
                selector=selectors.get(line, ()) if line else (),
            )
            if dependency is not None:
                found.append(dependency)
            continue
        if isinstance(item, dict):
            pip, issue = _pip_subsection(item, relative, line)
            found.extend(pip)
            if issue:
                issues.append(issue)
            continue
        issues.append(
            f"an entry of type {type(item).__name__} in 'dependencies' was skipped"
        )
    return tuple(found), issues


def _pip_subsection(
    item: dict, relative: str, line: int | None
) -> tuple[list[CondaDependency], str | None]:
    if "pip" not in item:
        return [], "a mapping entry in 'dependencies' is not a 'pip' subsection"
    values = item["pip"]
    if not isinstance(values, list):
        return [], "the 'pip' subsection is not a list"
    found: list[CondaDependency] = []
    for value in values:
        if not isinstance(value, str):
            continue
        dependency = parse_pip_requirement(value, file=relative, line=line)
        if dependency is not None:
            found.append(dependency)
    return found, None


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, (str, int, float)))


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _short(error: Exception) -> str:
    text = " ".join(str(error).split())
    return text[:200] if text else type(error).__name__


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _decode(raw: bytes) -> str | None:
    """Decode UTF-8, tolerating a byte-order mark and CRLF endings."""
    if raw.startswith(codecs.BOM_UTF8):
        raw = raw[len(codecs.BOM_UTF8) :]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def scan_conda(root: Path, *, explicitly_named: frozenset[str]) -> CondaScan:
    """Read every Conda environment declaration in ``root``."""
    environments: list[CondaEnvironment] = []
    candidates: list[str] = []
    for path in candidate_files(root, explicitly_named=explicitly_named):
        relative = path.relative_to(root).as_posix()
        candidates.append(relative)
        environments.append(parse_environment(path, root))
    return CondaScan(environments=environments, candidates=candidates)
