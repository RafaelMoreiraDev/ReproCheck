"""The FACT layer.

A *fact* is an observation extracted from the repository by a scanner. Facts
carry evidence (file, line, raw value) and nothing else: they never express an
opinion. Deterministic rules (:mod:`reprocheck.checks`) turn facts into findings.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from reprocheck.models import (
    DetectedFile,
    GitInfo,
    PackageManagerHint,
    ProjectScan,
    PythonRequirement,
    ReadmeCommand,
)

# Roles a package manager signal can play in a project.
ROLE_BUILD_BACKEND = "build-backend"
ROLE_INSTALLER = "installer"
ROLE_LOCKFILE = "lockfile"
ROLE_DEPENDENCY_DECLARATION = "dependency-declaration"

# Where a signal came from.
SOURCE_FILE = "file"
SOURCE_CONFIG = "configuration"
SOURCE_DOCS = "documentation"


def normalise_pattern(pattern: str) -> str:
    """Normalise a gitignore pattern for comparison purposes."""
    cleaned = pattern.replace("\\", "/").strip()
    while cleaned.endswith("/"):
        cleaned = cleaned[:-1]
    return cleaned.lower()


@dataclass(frozen=True, slots=True)
class PackageManagerSignal:
    """A package manager signal with its role and origin."""

    manager: str
    role: str
    evidence: str
    source: str = SOURCE_FILE

    def to_dict(self) -> dict[str, object]:
        return {
            "manager": self.manager,
            "role": self.role,
            "evidence": self.evidence,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class ReadmeReference:
    """A filesystem path referenced by a command documented in the README."""

    kind: str  # "requirements" | "script" | "test-path" | "directory"
    value: str
    command: str
    file: str
    line: int

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "value": self.value,
            "command": self.command,
            "file": self.file,
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class AbsolutePathRef:
    """An absolute local filesystem path found in a source or config file."""

    value: str
    style: str  # "windows" | "unix-home"
    file: str
    line: int

    def to_dict(self) -> dict[str, object]:
        return {
            "value": self.value,
            "style": self.style,
            "file": self.file,
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class FileReference:
    """A literal path passed to a well-known file-reading call."""

    call: str
    value: str
    file: str
    line: int
    resolved: str | None = None
    exists: bool | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "call": self.call,
            "value": self.value,
            "file": self.file,
            "line": self.line,
            "resolved": self.resolved,
            "exists": self.exists,
        }


@dataclass(frozen=True, slots=True)
class ToolSignal:
    """Evidence that a development tool is used by the project."""

    name: str
    evidence: str
    source: str = SOURCE_FILE

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "evidence": self.evidence, "source": self.source}


# --------------------------------------------------------------------------- #
# Dependency declarations
# --------------------------------------------------------------------------- #

# Dependency kinds.
KIND_RUNTIME = "runtime"
KIND_DEV = "dev"
KIND_TEST = "test"
KIND_OPTIONAL = "optional"
KIND_BUILD = "build"
KIND_CONSTRAINT = "constraint"

# Where a declaration was read from.
DEP_SOURCE_PYPROJECT = "pyproject"
DEP_SOURCE_REQUIREMENTS = "requirements"
DEP_SOURCE_DEPENDENCY_GROUP = "dependency-group"
DEP_SOURCE_BUILD_SYSTEM = "build-system"
DEP_SOURCE_POETRY = "poetry"
DEP_SOURCE_CONSTRAINT = "constraint"
#: A declaration read from the ``pip:`` subsection of a Conda environment.
#: It is PEP 508 text, but the origin is recorded so a report never claims a
#: Conda file is a requirements file.
DEP_SOURCE_CONDA_PIP = "conda-pip"

# How a dependency is obtained when it is not a registry name.
REF_URL = "url"
REF_VCS = "vcs"
REF_LOCAL_PATH = "local-path"

# How a ``uses:`` entry refers to something.
REF_ACTION = "action"
REF_REUSABLE_WORKFLOW = "reusable-workflow"
REF_LOCAL_ACTION = "local-action"
REF_LOCAL_WORKFLOW = "local-workflow"
REF_DOCKER = "docker"


@dataclass(frozen=True, slots=True)
class DependencyDeclaration:
    """A dependency exactly as the project declares it.

    Nothing is judged here: a declaration always keeps the raw text it came
    from, so a check can quote it verbatim.
    """

    name: str
    raw_name: str
    specifier: str = ""
    source: str = DEP_SOURCE_PYPROJECT
    group: str = KIND_RUNTIME
    kind: str = KIND_RUNTIME
    marker: str | None = None
    extras: tuple[str, ...] = ()
    file: str | None = None
    line: int | None = None
    raw: str = ""
    reference: str | None = None
    reference_kind: str | None = None
    vcs_ref: str | None = None
    vcs_commit: str | None = None

    @property
    def is_pinned(self) -> bool:
        """True when the specifier contains an exact ``==`` pin."""
        return any(
            part.strip().startswith("==")
            for part in self.specifier.split(",")
            if part.strip()
        )

    @property
    def is_bounded(self) -> bool:
        """True when any version constraint is present."""
        return bool(self.specifier.strip())

    @property
    def is_conditional(self) -> bool:
        """True when an environment marker makes the declaration conditional."""
        return bool(self.marker)

    @property
    def is_external(self) -> bool:
        """True when the dependency is not resolved from a package index."""
        return self.reference_kind is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "raw_name": self.raw_name,
            "specifier": self.specifier,
            "source": self.source,
            "group": self.group,
            "kind": self.kind,
            "marker": self.marker,
            "extras": list(self.extras),
            "file": self.file,
            "line": self.line,
            "raw": self.raw,
            "reference": self.reference,
            "reference_kind": self.reference_kind,
            "vcs_ref": self.vcs_ref,
            "vcs_commit": self.vcs_commit,
        }


@dataclass(frozen=True, slots=True)
class RequirementsInclude:
    """A ``-r``/``--requirement`` or ``-c``/``--constraint`` reference."""

    include_kind: str  # "include" | "constraint"
    value: str
    file: str
    line: int
    resolved: str | None = None
    exists: bool | None = None
    skipped_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "include_kind": self.include_kind,
            "value": self.value,
            "file": self.file,
            "line": self.line,
            "resolved": self.resolved,
            "exists": self.exists,
            "skipped_reason": self.skipped_reason,
        }


@dataclass(frozen=True, slots=True)
class WorkflowReference:
    """A ``uses:`` entry found in a GitHub Actions workflow.

    Nothing is resolved: the target and the ref are recorded exactly as the
    workflow spells them, and no remote is ever contacted.
    """

    file: str
    line: int
    raw: str
    target: str
    ref: str | None = None
    reference_type: str = REF_ACTION
    job: str | None = None
    step: str | None = None
    is_local: bool = False
    is_sha: bool = False
    is_mutable: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "file": self.file,
            "line": self.line,
            "raw": self.raw,
            "target": self.target,
            "ref": self.ref,
            "reference_type": self.reference_type,
            "job": self.job,
            "step": self.step,
            "is_local": self.is_local,
            "is_sha": self.is_sha,
            "is_mutable": self.is_mutable,
        }


@dataclass(frozen=True, slots=True)
class GitignoreInfo:
    """The parsed content of the project ``.gitignore``."""

    exists: bool = False
    file: str = ".gitignore"
    patterns: tuple[str, ...] = ()

    def ignores(self, pattern: str) -> bool:
        """Return ``True`` when ``pattern`` is covered by the ignore rules.

        Comparison is an exact match on the normalised pattern: V0.2 does not
        implement gitignore glob semantics.
        """
        return normalise_pattern(pattern) in self.patterns

    def to_dict(self) -> dict[str, object]:
        return {
            "exists": self.exists,
            "file": self.file,
            "patterns": list(self.patterns),
        }


# --------------------------------------------------------------------------- #
# Conda environment declarations
# --------------------------------------------------------------------------- #

#: The environment files ReproCheck treats as a Conda environment by name.
CONDA_ENVIRONMENT_FILENAMES = ("environment.yml", "environment.yaml")

#: A file name that may be a Conda environment, but only when something in the
#: project actually names it. No YAML is guessed to be an environment.
CONDA_ENVIRONMENT_PREFIXES = ("environment-", "environment_")

#: Where a Conda dependency came from inside the file.
CONDA_SOURCE_DEPENDENCY = "conda"
CONDA_SOURCE_PIP = "conda-pip"


@dataclass(frozen=True, slots=True)
class CondaDependency:
    """One entry of a Conda ``dependencies:`` list, exactly as declared.

    Conda version syntax is **not** PEP 440: ``numpy=1.26`` means the 1.26
    series, ``numpy 1.26*`` is a wildcard, and ``numpy=1.26=py311np123`` ends in
    a build string that is not a version at all. The original text is therefore
    kept verbatim in ``raw_spec``, and :attr:`pep440_specifier` holds a
    translation that is only produced where the two are provably the same set.
    """

    name: str
    raw: str
    source: str = CONDA_SOURCE_DEPENDENCY
    channel: str | None = None
    build: str | None = None
    raw_spec: str = ""
    pep440_specifier: str | None = None
    selector: tuple[str, ...] = ()
    file: str | None = None
    line: int | None = None
    is_python: bool = False

    @property
    def is_conditional(self) -> bool:
        """True when a platform selector makes the entry platform-specific.

        A conditional entry is never compared as if it applied everywhere: doing
        so would invent conflicts between platforms.
        """
        return bool(self.selector)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "raw": self.raw,
            "source": self.source,
            "channel": self.channel,
            "build": self.build,
            "raw_spec": self.raw_spec,
            "pep440_specifier": self.pep440_specifier,
            "selector": list(self.selector),
            "file": self.file,
            "line": self.line,
            "is_python": self.is_python,
        }


@dataclass(frozen=True, slots=True)
class CondaEnvironment:
    """One Conda environment declaration, as read from a single file.

    Nothing here is judged. ``parse_error`` and ``unsupported`` exist so a
    malformed or unrecognised structure is reported rather than silently
    treated as an empty environment.
    """

    file: str
    name: str | None = None
    channels: tuple[str, ...] = ()
    variables: tuple[str, ...] = ()
    dependencies: tuple[CondaDependency, ...] = ()
    parse_error: str | None = None
    unsupported: tuple[str, ...] = ()

    @property
    def is_parsed(self) -> bool:
        return self.parse_error is None

    @property
    def pip_dependencies(self) -> tuple[CondaDependency, ...]:
        return tuple(
            item for item in self.dependencies if item.source == CONDA_SOURCE_PIP
        )

    @property
    def conda_dependencies(self) -> tuple[CondaDependency, ...]:
        return tuple(
            item for item in self.dependencies if item.source == CONDA_SOURCE_DEPENDENCY
        )

    @property
    def python_dependency(self) -> CondaDependency | None:
        for item in self.dependencies:
            if item.is_python:
                return item
        return None

    @property
    def declares_pip(self) -> bool:
        """True when ``pip`` itself is one of the Conda dependencies."""
        return any(
            item.source == CONDA_SOURCE_DEPENDENCY and item.name == "pip"
            for item in self.dependencies
        )

    @property
    def has_pip_subsection(self) -> bool:
        return bool(self.pip_dependencies)

    def to_dict(self) -> dict[str, object]:
        return {
            "file": self.file,
            "name": self.name,
            "channels": list(self.channels),
            "variables": list(self.variables),
            "dependencies": [item.to_dict() for item in self.dependencies],
            "parse_error": self.parse_error,
            "unsupported": list(self.unsupported),
        }


@dataclass(slots=True)
class Facts:
    """Everything the scanners observed, ready to be checked."""

    project: ProjectScan
    git: GitInfo = field(default_factory=GitInfo)
    detected_files: list[DetectedFile] = field(default_factory=list)
    python_requirements: list[PythonRequirement] = field(default_factory=list)
    package_manager_hints: list[PackageManagerHint] = field(default_factory=list)
    package_manager_signals: list[PackageManagerSignal] = field(default_factory=list)
    readme_commands: list[ReadmeCommand] = field(default_factory=list)
    readme_references: list[ReadmeReference] = field(default_factory=list)
    absolute_paths: list[AbsolutePathRef] = field(default_factory=list)
    file_references: list[FileReference] = field(default_factory=list)
    tools: list[ToolSignal] = field(default_factory=list)
    distribution_name: str | None = None
    declared_dependencies: tuple[str, ...] = ()
    dynamic_fields: tuple[str, ...] = ()
    version_providers: tuple[str, ...] = ()
    dependency_declarations: list[DependencyDeclaration] = field(default_factory=list)
    requirement_includes: list[RequirementsInclude] = field(default_factory=list)
    workflow_references: list[WorkflowReference] = field(default_factory=list)
    gitignore: GitignoreInfo = field(default_factory=GitignoreInfo)
    conda_environments: list[CondaEnvironment] = field(default_factory=list)

    @property
    def has_conda(self) -> bool:
        """True when the project declares at least one Conda environment."""
        return bool(self.conda_environments)

    def has_file(self, relative: str) -> bool:
        """Return ``True`` when ``relative`` was detected as a file."""
        return any(
            item.path == relative and item.kind == "file"
            for item in self.detected_files
        )

    def has_directory(self, relative: str) -> bool:
        """Return ``True`` when ``relative`` was detected as a directory."""
        return any(
            item.path == relative and item.kind == "directory"
            for item in self.detected_files
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "package_manager_signals": _sorted(
                self.package_manager_signals, "manager", "role", "evidence"
            ),
            "readme_references": _sorted(
                self.readme_references, "file", "line", "kind", "value"
            ),
            "absolute_paths": _sorted(self.absolute_paths, "value", "file", "line"),
            "file_references": _sorted(
                self.file_references, "file", "line", "call", "value"
            ),
            "tools": _sorted(self.tools, "name", "evidence"),
            "gitignore": self.gitignore.to_dict(),
            "conda": {
                "environments": [
                    item.to_dict()
                    for item in sorted(
                        self.conda_environments, key=lambda entry: entry.file
                    )
                ]
            },
            "project_metadata": {
                "name": self.distribution_name,
                "dependencies": list(self.declared_dependencies),
                "dynamic_fields": list(self.dynamic_fields),
                "version_providers": list(self.version_providers),
            },
        }


def _sorted(items: list[object], *keys: str) -> list[dict[str, object]]:
    def key(item: object) -> tuple[str, ...]:
        data = item.to_dict()  # type: ignore[attr-defined]
        return tuple(str(data.get(name) or "") for name in keys)

    return [item.to_dict() for item in sorted(items, key=key)]  # type: ignore[attr-defined]
