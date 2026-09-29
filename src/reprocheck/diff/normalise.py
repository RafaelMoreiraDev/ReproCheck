"""Normalisation: what a comparison is allowed to see.

Two reports of the same project, taken minutes apart, differ in a dozen fields
that say nothing about reproducibility: the timestamp, the installation
duration, the temporary workspace path, the run id inside it, the log paths, the
interpreter executable, the order of two independent lists.

Every function here projects a report onto the facts that are **material**, and
returns a mapping ``identity -> attributes``. Identity is what decides whether
two entries are the same thing; the attributes are what is compared.

Deliberate omissions, and why:

``scan_timestamp``, ``reprocheck_version``, workspace/source/venv paths, run ids,
log paths, durations, exit codes, command lines, output snippets, interpreter
executables, list order.
    They describe the machine and the moment, not the project.
``line`` of a finding or reference.
    Inserting one line renumbers every finding below it. A moved finding is
    reported through its file and evidence, not through a number.
interpreter discovery reasons, pip output text.
    They quote local state and pip wording, both of which change by accident.

Two entries that share an identity are the same fact observed twice; two that
do not are two facts, whatever the wording says.
"""

from __future__ import annotations

import re

_SEPARATORS = re.compile(r"\s*[;|]\s*")
_WHITESPACE = re.compile(r"\s+")

#: How an entry is named for a reader. Never compared, never diffed.
LABEL_FIELD = "label"
#: A short human rendering of the entry. Never compared, never diffed.
DISPLAY_FIELD = "display"

#: Attributes that exist for presentation only.
_PRESENTATION_FIELDS = (LABEL_FIELD, DISPLAY_FIELD)

#: Finding fields kept for comparison and for the human report.
FINDING_FIELDS = (
    "id",
    "title",
    "severity",
    "confidence",
    "category",
    "message",
    "evidence",
    "file",
)


def normalise_text(value: object) -> str:
    """Collapse whitespace; case is preserved because names carry meaning."""
    if value is None:
        return ""
    return _WHITESPACE.sub(" ", str(value)).strip()


def normalise_evidence(evidence: object) -> str:
    """Normalise an evidence string: the order inside a list is incidental."""
    if not evidence:
        return ""
    parts = [
        _WHITESPACE.sub(" ", part).strip()
        for part in _SEPARATORS.split(str(evidence))
        if part.strip()
    ]
    return "; ".join(sorted(parts))


def changed_attributes(
    before: dict[str, str], after: dict[str, str]
) -> tuple[str, ...]:
    """Names of the attributes that differ, in alphabetical order."""
    ignored = set(_PRESENTATION_FIELDS)
    names = (set(before) | set(after)) - ignored
    return tuple(sorted(name for name in names if before.get(name) != after.get(name)))


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #


def normalise_findings(report: dict) -> dict[str, dict[str, str]]:
    """Findings by stable identity, with only material fields kept."""
    result: dict[str, dict[str, str]] = {}
    for finding in report.get("findings") or []:
        summary = {name: normalise_text(finding.get(name)) for name in FINDING_FIELDS}
        summary["evidence"] = normalise_evidence(finding.get("evidence"))
        subject = summary["evidence"] or summary.get("message") or ""
        subject = subject.split(";")[0].strip()
        summary[LABEL_FIELD] = f"{summary['id']} {subject}".strip()
        summary[DISPLAY_FIELD] = summary[LABEL_FIELD]
        identity = "|".join((summary["id"], summary["file"], summary["evidence"]))
        result[identity] = summary
    return result


# --------------------------------------------------------------------------- #
# Dependencies
# --------------------------------------------------------------------------- #


def normalise_dependencies(report: dict) -> dict[str, dict[str, str]]:
    """Dependency declarations by name, kind and group.

    The group belongs to the identity: moving a declaration from ``dev`` to
    ``project`` is a different declaration, and shows as one resolved plus one
    added rather than as an edit of a single fact.
    """
    result: dict[str, dict[str, str]] = {}
    declarations = (report.get("dependencies") or {}).get("declarations") or []
    for item in declarations:
        name = normalise_text(item.get("name")).lower()
        if not name:
            continue
        kind = normalise_text(item.get("kind"))
        group = normalise_text(item.get("group"))
        specifier = normalise_text(item.get("specifier"))
        reference = normalise_text(item.get("reference"))
        vcs_ref = normalise_text(item.get("vcs_ref")) or normalise_text(
            item.get("vcs_commit")
        )
        result[f"{name}|{kind}|{group}"] = {
            LABEL_FIELD: f"{name} ({group or kind or 'dependency'})",
            DISPLAY_FIELD: _dependency_display(
                name, group, specifier, reference, vcs_ref
            ),
            "specifier": specifier,
            "kind": kind,
            "reference_kind": normalise_text(item.get("reference_kind")),
            "reference": reference,
            "vcs_ref": vcs_ref,
            "source": normalise_text(item.get("source")),
        }
    return result


def _dependency_display(
    name: str, group: str, specifier: str, reference: str, vcs_ref: str
) -> str:
    version = specifier or "(no version constraint)"
    if reference:
        version = f"{version} via {reference}" if specifier else f"from {reference}"
    if vcs_ref:
        version = f"{version} at {vcs_ref}"
    return f"{name} ({group or 'dependency'}) {version}"


# --------------------------------------------------------------------------- #
# Conda
# --------------------------------------------------------------------------- #


def normalise_conda(report: dict) -> dict[str, dict[str, str]]:
    """Conda facts, keyed so that each kind of change is distinguishable.

    Three key spaces share one namespace, joined by a prefix that cannot occur
    in a filename:

    ``env:<file>``
        the environment itself: its name, its Python spec, its channels and
        whether it is readable. An environment added or removed is one change,
        and a field that changed is an edit of that one fact.
    ``dep:<file>:<name>``
        one dependency, so adding or removing a package is one change and
        changing its spec is an edit.
    ``pip:<file>:<name>``
        the same, for the ``pip:`` subsection.

    Channels are sorted and compared as a set, because the order conda resolves
    them in is not a fact about the declaration. Comments and formatting are
    never compared: only the parsed values are.
    """
    result: dict[str, dict[str, str]] = {}
    section = report.get("conda") or {}
    for environment in section.get("environments") or []:
        file = normalise_text(environment.get("file"))
        python = next(
            (
                item
                for item in environment.get("dependencies") or []
                if item.get("is_python")
            ),
            None,
        )
        channels = environment.get("channels") or []
        result[f"env:{file}"] = {
            LABEL_FIELD: f"Conda environment {file}",
            DISPLAY_FIELD: f"{file} (name: {environment.get('name') or '-'})",
            "name": normalise_text(environment.get("name")),
            "python": normalise_text(python.get("raw")) if python else "",
            "channels": ",".join(sorted(str(item) for item in channels)),
            "readable": "" if environment.get("parse_error") else "yes",
        }
        for dependency in environment.get("dependencies") or []:
            name = normalise_text(dependency.get("name"))
            if not name:
                continue
            prefix = "pip" if dependency.get("source") == "conda-pip" else "dep"
            key = f"{prefix}:{file}:{name}"
            selector = "/".join(dependency.get("selector") or [])
            channel = normalise_text(dependency.get("channel"))
            build = normalise_text(dependency.get("build"))
            spec = normalise_text(dependency.get("raw_spec"))
            display = dependency.get("raw") or name
            if channel:
                display = f"{channel}::{display}"
            result[key] = {
                LABEL_FIELD: f"{name} ({file})",
                DISPLAY_FIELD: f"{name} {spec or '(no constraint)'}".strip(),
                "raw": normalise_text(dependency.get("raw")),
                "specifier": spec,
                "selector": selector,
                "channel": channel,
                "build": build,
            }
    return result


# --------------------------------------------------------------------------- #
# Python
# --------------------------------------------------------------------------- #


def normalise_python(report: dict) -> dict[str, dict[str, str]]:
    """Python declarations by source, so a changed requirement is an edit.

    A source that declares several versions (a CI matrix) is numbered after
    sorting, because no single one of them is *the* declaration of that source.
    """
    result: dict[str, dict[str, str]] = {}
    grouped: dict[str, list[dict]] = {}
    for item in report.get("python_requirements") or []:
        grouped.setdefault(normalise_text(item.get("source")), []).append(item)

    for source, items in grouped.items():
        items.sort(key=lambda item: normalise_text(item.get("value")))
        multiple = len(items) > 1
        for index, item in enumerate(items, start=1):
            value = normalise_text(item.get("value"))
            key = f"{source}#{index}" if multiple else source
            result[key] = {
                LABEL_FIELD: f"{source}: {value}" if multiple else source,
                DISPLAY_FIELD: f"{source} = {value}",
                "value": value,
                "file": normalise_text(item.get("file")),
            }
    return result


# --------------------------------------------------------------------------- #
# CI
# --------------------------------------------------------------------------- #


def normalise_ci(report: dict) -> dict[str, dict[str, str]]:
    """Workflow references by target and file, so a new ref is an edit."""
    result: dict[str, dict[str, str]] = {}
    grouped: dict[tuple[str, str], list[dict]] = {}
    for item in report.get("workflow_references") or []:
        target = normalise_text(item.get("target"))
        file = normalise_text(item.get("file"))
        grouped.setdefault((target, file), []).append(item)

    for (target, file), items in grouped.items():
        items.sort(key=lambda item: normalise_text(item.get("ref")))
        multiple = len(items) > 1
        for index, item in enumerate(items, start=1):
            ref = normalise_text(item.get("ref"))
            kind = (
                "local"
                if item.get("is_local")
                else "full commit SHA"
                if item.get("is_sha")
                else "mutable reference"
            )
            key = f"{target}|{file}#{index}" if multiple else f"{target}|{file}"
            result[key] = {
                LABEL_FIELD: f"{target}@{ref} in {file}",
                DISPLAY_FIELD: f"{target}@{ref} ({kind}) in {file}",
                "target": target,
                "ref": ref,
                "reference_type": normalise_text(item.get("reference_type")),
                "immutability": kind,
            }
    return result


# --------------------------------------------------------------------------- #
# Reproduction
# --------------------------------------------------------------------------- #


def normalise_reproduction(report: dict) -> dict[str, dict[str, str]]:
    """One reproduction run as a flat set of labelled scalar facts."""
    reproduction = report.get("reproduction") or {}
    if not reproduction.get("attempted"):
        return {}
    result: dict[str, dict[str, str]] = {}

    def add(key: str, label: str, display: str, **attributes: str) -> None:
        result[key] = {
            LABEL_FIELD: label,
            DISPLAY_FIELD: display,
            **attributes,
        }

    add("attempted", "a reproduction was attempted", "yes")
    add(
        "network_enabled",
        "network allowed for the installation",
        _bool(reproduction.get("network_enabled")),
        value=_bool(reproduction.get("network_enabled")),
    )
    add(
        "runtime_checks.enabled",
        "runtime checks requested",
        _bool(reproduction.get("runtime_checks_enabled")),
        value=_bool(reproduction.get("runtime_checks_enabled")),
    )

    selection = reproduction.get("python") or {}
    if selection.get("selected"):
        value = normalise_text(selection.get("selected"))
        add("python.selected", "selected Python", value, value=value)

    installation = reproduction.get("installation")
    if installation:
        strategy = normalise_text(installation.get("strategy"))
        add("installation.strategy", "installation strategy", strategy, value=strategy)
        success = _bool(installation.get("success"))
        add("installation.success", "installation completed", success, value=success)

    pip_check = reproduction.get("pip_check") or {}
    if pip_check.get("ran"):
        clean = _bool(pip_check.get("clean"))
        add("pip_check.clean", "pip check", clean, value=clean)
        conflicts = normalise_text(pip_check.get("conflict_count"))
        add(
            "pip_check.conflict_count",
            "pip check conflicts",
            conflicts,
            value=conflicts,
        )

    distribution = reproduction.get("installed_distribution") or {}
    if distribution.get("name"):
        name = normalise_text(distribution.get("name"))
        add("installed_distribution.name", "installed distribution", name, value=name)
        version = normalise_text(distribution.get("version"))
        add(
            "installed_distribution.version",
            "installed version",
            version,
            value=version,
        )
        fallback = _bool(distribution.get("looks_like_fallback"))
        add(
            "installed_distribution.looks_like_fallback",
            "installed version looks like a fallback",
            fallback,
            value=fallback,
        )

    runtime = reproduction.get("runtime_checks") or {}
    for item in runtime.get("imports") or []:
        module = normalise_text(item.get("module"))
        imported = bool(item.get("imported"))
        timed_out = bool(item.get("timed_out"))
        state = "timed out" if timed_out else "imported" if imported else "failed"
        add(
            f"imports.{module}",
            f"import {module}",
            state,
            imported=_bool(imported),
            timed_out=_bool(timed_out),
        )

    collection = runtime.get("pytest_collection") or {}
    for name in ("available", "success", "collected"):
        if collection.get(name) is not None:
            value = normalise_text(collection.get(name))
            add(
                f"pytest_collection.{name}",
                f"pytest collection {name}",
                value,
                value=value,
            )

    integrity = reproduction.get("integrity") or {}
    if integrity.get("captured_before"):
        unchanged = _bool(integrity.get("unchanged"))
        add(
            "integrity.unchanged",
            "original source tree unchanged",
            unchanged,
            value=unchanged,
        )
        changed = sorted(
            normalise_text(path) for path in integrity.get("changed_paths") or []
        )
        add(
            "integrity.changed_paths",
            "changed paths in the original project",
            ", ".join(changed) if changed else "(none)",
            value=", ".join(changed) if changed else "(none)",
        )
    return result


def _bool(value: object) -> str:
    if value is None:
        return "unknown"
    return "true" if value else "false"
