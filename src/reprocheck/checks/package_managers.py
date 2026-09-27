"""Deterministic reconciliation of package manager signals (RC110-RC113).

Signals are classified by role before any judgement is made. Combinations that
are legitimate by design (a build backend plus an installer, for instance
``setuptools`` with ``uv`` or with ``pip``) never produce a finding.
"""

from __future__ import annotations

import re

from reprocheck.facts import (
    ROLE_INSTALLER,
    ROLE_LOCKFILE,
    SOURCE_CONFIG,
    SOURCE_DOCS,
    Facts,
)
from reprocheck.models import Confidence, Finding, Severity

RC110_MULTIPLE_LOCKFILES = "RC110"
RC111_LOCKFILE_WITHOUT_CONFIG = "RC111"
RC112_DOCUMENTED_TOOL_NOT_CONFIGURED = "RC112"
RC113_ORPHAN_LOCKFILE = "RC113"
RC114_DOCUMENTED_INSTALL_IGNORES_LOCKFILE = "RC114"
RC115_UNDECLARED_DOCUMENTED_INSTALL = "RC115"
RC116_CONFIGURED_TOOL_WITHOUT_LOCKFILE = "RC116"

# A lockfile whose declaration file is missing is an unambiguous problem.
_ORPHAN_LOCKFILES = {"Pipfile.lock": "Pipfile"}

# Tools that have a well-known lockfile. pip is excluded on purpose: a
# requirements file already is its resolved form.
_LOCKFILE_BY_MANAGER = {
    "uv": "uv.lock",
    "poetry": "poetry.lock",
    "pipenv": "Pipfile.lock",
    "pdm": "pdm.lock",
}

# Installers whose documented usage does not consume a lockfile.
_PIP_INSTALLERS = frozenset({"pip"})

_EXTERNAL_INSTALL_TOKENS = frozenset(
    {"pip", "pip3", "conda", "mamba", "micromamba", "uv", "uvx", "poetry", "pipenv"}
)
_SKIP_INSTALL_TARGETS = frozenset({"install", "env", "create", "run", "sync", "add"})


def check_package_manager_roles(facts: Facts) -> list[Finding]:
    """Apply every package manager reconciliation rule."""
    return [
        *_orphan_lockfiles(facts),
        *_multiple_lockfiles(facts),
        *_lockfiles_without_configuration(facts),
        *_documented_tools_not_configured(facts),
        *_documented_install_ignores_lockfile(facts),
        *_undeclared_documented_installs(facts),
        *_configured_tools_without_lockfile(facts),
    ]


def _lockfiles(facts: Facts) -> list[tuple[str, str]]:
    """Return ``(manager, evidence)`` for every lockfile signal."""
    return sorted(
        (signal.manager, signal.evidence)
        for signal in facts.package_manager_signals
        if signal.role == ROLE_LOCKFILE
    )


def _configured_managers(facts: Facts) -> set[str]:
    """Managers with an explicit configuration section in the project."""
    return {
        signal.manager
        for signal in facts.package_manager_signals
        if signal.role == ROLE_INSTALLER and signal.source == SOURCE_CONFIG
    }


def _documented_managers(facts: Facts) -> set[str]:
    """Managers the documentation tells the reader to use."""
    return {
        signal.manager
        for signal in facts.package_manager_signals
        if signal.role == ROLE_INSTALLER and signal.source == SOURCE_DOCS
    }


def _orphan_lockfiles(facts: Facts) -> list[Finding]:
    findings: list[Finding] = []
    for signal in facts.package_manager_signals:
        if signal.role != ROLE_LOCKFILE:
            continue
        required = _ORPHAN_LOCKFILES.get(signal.evidence)
        if required is None or facts.has_file(required):
            continue
        findings.append(
            Finding(
                id=RC113_ORPHAN_LOCKFILE,
                title="Lockfile present without its declaration file",
                severity=Severity.WARNING,
                category="packaging",
                message=(
                    f"{signal.evidence} exists but {required} does not, so the "
                    "locked dependencies cannot be resolved from the repository."
                ),
                evidence=signal.evidence,
                file=signal.evidence,
                confidence=Confidence.HIGH,
            )
        )
    return findings


def _multiple_lockfiles(facts: Facts) -> list[Finding]:
    lockfiles = _lockfiles(facts)
    managers = {manager for manager, _ in lockfiles}
    if len(managers) < 2:
        return []
    return [
        Finding(
            id=RC110_MULTIPLE_LOCKFILES,
            title="Multiple lockfiles from different tools",
            severity=Severity.WARNING,
            category="packaging",
            message=(
                "Lockfiles from more than one tool are present ("
                + ", ".join(sorted(managers))
                + "). Which one is authoritative cannot be proven from the "
                "repository alone."
            ),
            evidence=", ".join(evidence for _, evidence in lockfiles),
            confidence=Confidence.MEDIUM,
        )
    ]


def _lockfiles_without_configuration(facts: Facts) -> list[Finding]:
    known = _configured_managers(facts) | _documented_managers(facts)
    findings: list[Finding] = []
    for manager, evidence in _lockfiles(facts):
        if manager in known:
            continue
        findings.append(
            Finding(
                id=RC111_LOCKFILE_WITHOUT_CONFIG,
                title="Lockfile without configuration for its tool",
                severity=Severity.WARNING,
                category="packaging",
                message=(
                    f"{evidence} exists but no configuration or documented usage "
                    f"of {manager} was found; the lockfile may be stale."
                ),
                evidence=evidence,
                file=evidence,
                confidence=Confidence.MEDIUM,
            )
        )
    return findings


def _documented_tools_not_configured(facts: Facts) -> list[Finding]:
    configured = _configured_managers(facts)
    locked = {manager for manager, _ in _lockfiles(facts)}
    findings: list[Finding] = []
    for signal in facts.package_manager_signals:
        if signal.role != ROLE_INSTALLER or signal.source != SOURCE_DOCS:
            continue
        if signal.manager in configured or signal.manager in locked:
            continue
        findings.append(
            Finding(
                id=RC112_DOCUMENTED_TOOL_NOT_CONFIGURED,
                title="Documented package manager is not configured",
                severity=Severity.WARNING,
                category="packaging",
                message=(
                    f"The documentation tells the reader to use {signal.manager}, "
                    f"but the project has no {signal.manager} configuration and "
                    f"no {signal.manager} lockfile."
                ),
                evidence=signal.evidence,
                confidence=Confidence.MEDIUM,
            )
        )
    return findings


def _documented_install_ignores_lockfile(facts: Facts) -> list[Finding]:
    """Report documented installs that cannot consume the project lockfile."""
    lockfiles = _lockfiles(facts)
    if not lockfiles:
        return []
    owners = sorted({manager for manager, _ in lockfiles})
    if set(owners) & _PIP_INSTALLERS:
        return []

    commands = [
        command for command in facts.readme_commands if _is_pip_install(command.command)
    ]
    if not commands:
        return []

    first = commands[0]
    return [
        Finding(
            id=RC114_DOCUMENTED_INSTALL_IGNORES_LOCKFILE,
            title="Documented install does not use the project lockfile",
            severity=Severity.INFO,
            category="packaging",
            message=(
                f"The documentation installs with pip ({len(commands)} command(s)) "
                f"but the only lockfile belongs to {', '.join(owners)}; an install "
                "performed as documented is not resolved from that lockfile."
            ),
            evidence="; ".join(
                f"{command.file}:{command.line} ({command.command})"
                for command in commands
            ),
            file=first.file,
            line=first.line,
            confidence=Confidence.MEDIUM,
        )
    ]


def _undeclared_documented_installs(facts: Facts) -> list[Finding]:
    """Report documented package installs absent from the project metadata."""
    if not facts.distribution_name:
        return []
    declared = set(facts.declared_dependencies)
    own_name = facts.distribution_name.strip().lower().replace("_", "-")

    grouped: dict[str, list[str]] = {}
    for command in facts.readme_commands:
        for target in _install_targets(command.command):
            if target in declared or target == own_name or target in grouped:
                continue
            grouped[target] = [f"{command.file}:{command.line} ({command.command})"]

    return [
        Finding(
            id=RC115_UNDECLARED_DOCUMENTED_INSTALL,
            title="Documented package install is not declared in metadata",
            severity=Severity.WARNING,
            category="packaging",
            message=(
                f"The documentation installs '{target}', which is not declared in "
                "the project dependencies; it may be a system or transitive "
                "requirement."
            ),
            evidence="; ".join(locations),
            confidence=Confidence.MEDIUM,
        )
        for target, locations in sorted(grouped.items())
    ]


def _configured_tools_without_lockfile(facts: Facts) -> list[Finding]:
    """Report configured tools that ship no lockfile."""
    locked = {manager for manager, _ in _lockfiles(facts)}
    findings: list[Finding] = []
    for signal in facts.package_manager_signals:
        if signal.role != ROLE_INSTALLER or signal.source != SOURCE_CONFIG:
            continue
        expected = _LOCKFILE_BY_MANAGER.get(signal.manager)
        if expected is None or signal.manager in locked:
            continue
        findings.append(
            Finding(
                id=RC116_CONFIGURED_TOOL_WITHOUT_LOCKFILE,
                title="Configured dependency tool ships no lockfile",
                severity=Severity.WARNING,
                category="packaging",
                message=(
                    f"{signal.evidence} configures {signal.manager}, but "
                    f"{expected} is not present; dependency versions are not "
                    "pinned by the repository."
                ),
                evidence=signal.evidence,
                file="pyproject.toml",
                confidence=Confidence.HIGH,
            )
        )
    return findings


def _is_pip_install(command: str) -> bool:
    tokens = [token.lower() for token in command.replace("|", " ").split()]
    return len(tokens) >= 2 and tokens[0] in {"pip", "pip3"} and tokens[1] == "install"


def _install_targets(command: str) -> list[str]:
    """Return the distribution names a documented install command adds.

    Values that belong to a command-line option (``-c conda-forge``) are not
    distribution names, so the token following an option is skipped.
    """
    tokens = [token.lower() for token in command.replace("|", " ").split()]
    if len(tokens) < 2 or tokens[0] not in _EXTERNAL_INSTALL_TOKENS:
        return []
    targets: list[str] = []
    previous_is_option = False
    for token in tokens[2:]:
        if token.startswith("-"):
            previous_is_option = True
            continue
        if previous_is_option or token in _SKIP_INSTALL_TARGETS:
            previous_is_option = False
            continue
        previous_is_option = False
        if any(char in token for char in ("/", "\\", "=", ":", ">", "<")):
            continue
        name = token.split("[", 1)[0]
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name):
            targets.append(name)
    return targets
