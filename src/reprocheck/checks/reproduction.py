"""Findings produced by a reproduction attempt (RC400-RC407).

These checks read the reproduction report only. They never re-run anything, and
they never claim more than the run observed: an installation that failed
because the network was disabled is reported as such, not as a broken project.
"""

from __future__ import annotations

from reprocheck.models import Confidence, Finding, Severity
from reprocheck.reproduction.conda import (
    STOP_AMBIGUOUS_ENVIRONMENT,
    STOP_NO_ENVIRONMENT_FILE,
    STOP_NO_MANAGER,
    STOP_NO_NETWORK,
    STOP_UNKNOWN_MANAGER,
)
from reprocheck.reproduction.installer import needs_network
from reprocheck.reproduction.models import ReproductionReport, snippet

RC400_PIP_CHECK_CONFLICTS = "RC400"
RC401_INSTALLATION_FAILED = "RC401"
RC402_PYTHON_NOT_INSTALLED = "RC402"
RC403_NO_INSTALLATION_STRATEGY = "RC403"
RC404_NETWORK_REQUIRED = "RC404"
RC405_PYTHON_AMBIGUOUS = "RC405"
RC406_ORIGINAL_MODIFIED = "RC406"
RC407_VENV_FAILED = "RC407"
RC600_CONDA_MANAGER_MISSING = "RC600"
RC601_CONDA_AMBIGUOUS = "RC601"
RC602_CONDA_CREATE_FAILED = "RC602"
RC603_CONDA_PYTHON_MISMATCH = "RC603"
RC604_CONDA_NETWORK_REQUIRED = "RC604"
RC500_IMPORT_FAILED = "RC500"
RC501_TEST_COLLECTION_FAILED = "RC501"
RC502_IMPORT_TIMEOUT = "RC502"

CATEGORY = "reproduction"

_INFO_CHARS = 600


def check_reproduction(
    report: ReproductionReport, *, reprocheck_version: str = "0.0.0"
) -> list[Finding]:
    """Return every finding produced by one reproduction attempt."""
    return [
        *_python_findings(report),
        *_venv_findings(report),
        *_installation_findings(report),
        *_pip_check_findings(report),
        *_import_findings(report),
        *_collection_findings(report),
        *_integrity_findings(report),
        *_conda_findings(report),
    ]


def _python_findings(report: ReproductionReport) -> list[Finding]:
    """Interpreter selection, which only the pip pipeline performs.

    A Conda attempt never selects an interpreter: the environment file names the
    Python and the manager provides it. Reporting "no deterministic version
    could be chosen" for a Conda run would be a statement about a step that was
    not part of it.
    """
    if report.conda is not None:
        return []
    selection = report.python
    if selection.selected is not None:
        return []
    available = ", ".join(item.version for item in selection.candidates) or "none"
    requirement = selection.requirement
    missing = (
        f"the project requires {requirement}"
        if requirement
        else "no deterministic version could be chosen"
    )
    return [
        Finding(
            id=RC402_PYTHON_NOT_INSTALLED if requirement else RC405_PYTHON_AMBIGUOUS,
            title=(
                "Required Python version is not installed locally"
                if requirement
                else "Python version could not be selected deterministically"
            ),
            severity=Severity.ERROR,
            category=CATEGORY,
            message=(
                f"Reproduction was not attempted: {missing}. "
                f"{selection.reason}. Interpreters found locally: {available}."
            ),
            evidence=selection.reason,
            confidence=Confidence.HIGH,
        )
    ]


def _import_findings(report: ReproductionReport) -> list[Finding]:
    """One finding per module that could not be imported."""
    findings: list[Finding] = []
    for check in report.imports:
        if check.imported:
            continue
        if check.timed_out:
            findings.append(
                Finding(
                    id=RC502_IMPORT_TIMEOUT,
                    title="Module import timed out",
                    severity=Severity.ERROR,
                    category=CATEGORY,
                    message=(
                        f"Importing '{check.module}' did not finish within the "
                        f"per-module timeout ({check.duration_seconds}s elapsed) "
                        "and the process tree was terminated."
                    ),
                    evidence=(f"module: {check.module} | log: {check.stderr_path}"),
                    confidence=Confidence.HIGH,
                )
            )
            continue
        findings.append(
            Finding(
                id=RC500_IMPORT_FAILED,
                title="Installed module could not be imported",
                severity=Severity.ERROR,
                category=CATEGORY,
                message=(
                    f"The installed distribution provides the top-level module "
                    f"'{check.module}', but importing it failed with exit code "
                    f"{check.exit_code}."
                ),
                evidence=(
                    f"module: {check.module} | log: {check.stderr_path} | "
                    f"{check.error or ''}"
                ),
                confidence=Confidence.HIGH,
            )
        )
    return findings


def _collection_findings(report: ReproductionReport) -> list[Finding]:
    collection = report.test_collection
    if not collection.available or not collection.ran or collection.success:
        return []
    collected = (
        f"{collection.collected} item(s) collected"
        if collection.collected is not None
        else "the number of collected items could not be parsed"
    )
    return [
        Finding(
            id=RC501_TEST_COLLECTION_FAILED,
            title="Test collection failed in the reproduced environment",
            severity=Severity.WARNING,
            category=CATEGORY,
            message=(
                "pytest is installed in the reproduced environment but "
                f"'--collect-only' failed with exit code {collection.exit_code} "
                f"({collected}). Tests were not executed."
            ),
            evidence=(
                f"log: {collection.stdout_path or collection.stderr_path} | "
                f"{collection.stderr_snippet or collection.stdout_snippet}"
            ),
            confidence=Confidence.HIGH,
        )
    ]


def _venv_findings(report: ReproductionReport) -> list[Finding]:
    if report.venv.created or not report.venv.path:
        return []
    return [
        Finding(
            id=RC407_VENV_FAILED,
            title="Virtual environment could not be created",
            severity=Severity.ERROR,
            category=CATEGORY,
            message=(
                "The virtual environment could not be created with "
                f"{report.python.executable}; the reproduction stopped there."
            ),
            evidence=report.venv.error or "",
            confidence=Confidence.HIGH,
        )
    ]


def _installation_findings(report: ReproductionReport) -> list[Finding]:
    attempt = report.installation
    if attempt is None:
        # No strategy: the attempt never produced an InstallationAttempt.
        if "no installation strategy" in report.completed_steps:
            return [
                Finding(
                    id=RC403_NO_INSTALLATION_STRATEGY,
                    title="No deterministic installation strategy found",
                    severity=Severity.WARNING,
                    category=CATEGORY,
                    message=(
                        "Reproduction stopped: the project declares neither an "
                        "installable package nor a requirements.txt, so no "
                        "installation was attempted."
                    ),
                    evidence="no pyproject.toml [project]/setup.py and no requirements.txt",
                    confidence=Confidence.HIGH,
                )
            ]
        return []

    if attempt.success:
        return []

    command = " ".join(attempt.command)
    detail = snippet(attempt.stderr_snippet or attempt.stdout_snippet, _INFO_CHARS)
    findings = [
        Finding(
            id=RC401_INSTALLATION_FAILED,
            title="Project installation failed",
            severity=Severity.ERROR,
            category=CATEGORY,
            message=(
                f"Installation with the '{attempt.strategy}' strategy failed with "
                f"exit code {attempt.exit_code}: {detail}"
            ),
            evidence=(
                f"command: {command} | cwd: {attempt.cwd} | "
                f"network: {'enabled' if attempt.network_enabled else 'disabled'} | "
                f"log: {attempt.stderr_path or attempt.stdout_path}"
            ),
            confidence=Confidence.HIGH,
        )
    ]
    if not attempt.network_enabled and needs_network(attempt):
        findings.append(
            Finding(
                id=RC404_NETWORK_REQUIRED,
                title="Installation appears to require network access",
                severity=Severity.INFO,
                category=CATEGORY,
                message=(
                    "The install output shows the dependencies were not available "
                    "locally. Re-run with --network to allow the installation step "
                    "to reach a package index."
                ),
                evidence=detail,
                confidence=Confidence.HIGH,
            )
        )
    return findings


def _pip_check_findings(report: ReproductionReport) -> list[Finding]:
    result = report.pip_check
    if not result.ran or not result.conflicts:
        return []
    listed = "; ".join(result.conflicts[:10])
    more = (
        f" (+{result.conflict_count - 10} more)" if result.conflict_count > 10 else ""
    )
    return [
        Finding(
            id=RC400_PIP_CHECK_CONFLICTS,
            title="Installed environment has dependency conflicts",
            severity=Severity.ERROR,
            category=CATEGORY,
            message=(
                f"pip check reported {result.conflict_count} conflict(s) in the "
                f"reproduced environment: {listed}{more}."
            ),
            evidence=snippet(result.stdout_snippet, _INFO_CHARS),
            confidence=Confidence.HIGH,
        )
    ]


def _integrity_findings(report: ReproductionReport) -> list[Finding]:
    integrity = report.integrity
    if integrity.unchanged:
        return []
    listed = "; ".join(integrity.changed_paths[:10])
    more = (
        f" (+{len(integrity.changed_paths) - 10} more)"
        if len(integrity.changed_paths) > 10
        else ""
    )
    return [
        Finding(
            id=RC406_ORIGINAL_MODIFIED,
            title="The analysed project changed during reproduction",
            severity=Severity.ERROR,
            category=CATEGORY,
            message=(
                "ReproCheck detected changes in the analysed project while "
                f"reproducing it, which is a violation of its read-only "
                f"guarantee: {listed}{more}."
            ),
            evidence=(
                f"git head before: {integrity.git_head_before} | after: "
                f"{integrity.git_head_after}"
            ),
            confidence=Confidence.HIGH,
        )
    ]


# --------------------------------------------------------------------------- #
# Conda
# --------------------------------------------------------------------------- #


def _conda_findings(report: ReproductionReport) -> list[Finding]:
    """Every finding produced by a Conda attempt.

    The four refusal cases come first and are mutually exclusive: an attempt
    that never created an environment cannot also have a wrong Python in it.
    """
    attempt = report.conda
    if attempt is None or not attempt.attempted:
        return []

    if attempt.reason == STOP_NO_MANAGER:
        checked = "; ".join(attempt.discovery) or "none"
        return [
            Finding(
                id=RC600_CONDA_MANAGER_MISSING,
                title="Conda-compatible environment manager is not available",
                severity=Severity.ERROR,
                category=CATEGORY,
                message=(
                    "A Conda reproduction was requested but no supported manager is "
                    "installed. ReproCheck does not install one: downloading a "
                    "package manager would be a larger action than the "
                    f"reproduction, and it would not be visible in the report. "
                    f"Checked: {checked}."
                ),
                evidence=attempt.error,
                confidence=Confidence.HIGH,
            )
        ]

    if attempt.reason in {STOP_AMBIGUOUS_ENVIRONMENT, STOP_NO_ENVIRONMENT_FILE}:
        listing = ", ".join(attempt.discovery) or "none"
        return [
            Finding(
                id=RC601_CONDA_AMBIGUOUS,
                title=(
                    "Multiple Conda environment files detected"
                    if attempt.reason == STOP_AMBIGUOUS_ENVIRONMENT
                    else "No Conda environment file was found"
                ),
                severity=Severity.ERROR,
                category=CATEGORY,
                message=(
                    f"{attempt.error or ''} Nothing was run. Candidates: {listing}."
                ).strip(),
                evidence=attempt.error,
                confidence=Confidence.HIGH,
            )
        ]

    if attempt.reason == STOP_UNKNOWN_MANAGER:
        return [
            Finding(
                id=RC600_CONDA_MANAGER_MISSING,
                title="The requested Conda manager is not supported",
                severity=Severity.ERROR,
                category=CATEGORY,
                message=str(attempt.error or ""),
                evidence=attempt.error,
                confidence=Confidence.HIGH,
            )
        ]

    if attempt.reason == STOP_NO_NETWORK:
        return [
            Finding(
                id=RC604_CONDA_NETWORK_REQUIRED,
                title="Conda reproduction requires network permission",
                severity=Severity.INFO,
                category=CATEGORY,
                message=(
                    "A Conda environment was requested but the run did not have "
                    "network permission, so no environment was created. Conda "
                    "resolves packages from channels by default, and ReproCheck "
                    "does not assume an offline solve is possible."
                ),
                evidence=attempt.error,
                confidence=Confidence.HIGH,
            )
        ]

    if not attempt.success:
        return [
            Finding(
                id=RC602_CONDA_CREATE_FAILED,
                title="The Conda environment could not be created",
                severity=Severity.ERROR,
                category=CATEGORY,
                message=(
                    f"{attempt.manager} could not create the environment from "
                    f"{attempt.environment_file} (exit code {attempt.exit_code}): "
                    f"{attempt.error or 'no output'}"
                ),
                evidence=(
                    f"manager: {attempt.manager} {attempt.manager_version or ''} | "
                    f"file: {attempt.environment_file} | prefix: {attempt.prefix} | "
                    f"command: {' '.join(attempt.command) or '-'} | "
                    f"log: {attempt.stderr_path or attempt.stdout_path or '-'}"
                ),
                confidence=Confidence.HIGH,
            )
        ]

    mismatch = _python_mismatch(attempt)
    if mismatch:
        return [
            Finding(
                id=RC603_CONDA_PYTHON_MISMATCH,
                title="The environment Python does not match what the file declares",
                severity=Severity.ERROR,
                category=CATEGORY,
                message=(
                    f"{attempt.environment_file} declares '{attempt.declared_python}' "
                    f"but the created environment provides Python "
                    f"{attempt.python_version}."
                ),
                evidence=(
                    f"declared: {attempt.declared_python} | "
                    f"installed: {attempt.python_version}"
                ),
                file=attempt.environment_file,
                confidence=Confidence.HIGH,
            )
        ]

    conflicts = attempt.pip_check
    if conflicts.ran and conflicts.conflicts:
        return [
            Finding(
                id=RC400_PIP_CHECK_CONFLICTS,
                title="pip check found broken requirements in the Conda environment",
                severity=Severity.ERROR,
                category=CATEGORY,
                message=(
                    "The environment was created, but pip check reported "
                    f"{conflicts.conflict_count} conflict(s) inside it: "
                    + "; ".join(conflicts.conflicts[:5])
                ),
                evidence="; ".join(conflicts.conflicts),
                file=attempt.environment_file,
                confidence=Confidence.HIGH,
            )
        ]
    return []


def _python_mismatch(attempt) -> str | None:
    """Return a mismatch description, or `None` when the pair is compatible.

    The environment's own interpreter is the authority, and the declared spec
    is only compared when it can be translated to PEP 440. A declaration the
    project wrote in a form with no exact equivalent is reported as unknown
    rather than as a mismatch.
    """
    if not attempt.python_version or not attempt.declared_python:
        return None
    from reprocheck.checks.conda import widen_wildcard_pin
    from reprocheck.scanners.conda import conda_to_pep440

    translated = conda_to_pep440(attempt.declared_python)
    if translated is None:
        return None
    from packaging.version import InvalidVersion, Version

    try:
        version = Version(attempt.python_version)
    except InvalidVersion:
        return None
    specifier = widen_wildcard_pin(translated)
    from packaging.specifiers import InvalidSpecifier, SpecifierSet

    try:
        allowed = SpecifierSet(specifier)
    except InvalidSpecifier:
        return None
    if version in allowed:
        return None
    return attempt.declared_python
