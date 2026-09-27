"""Deterministic checks.

Each module exposes plain functions of :class:`~reprocheck.facts.Facts`. The
order of :data:`ALL_CHECKS` defines the order of the findings in the report.
"""

from reprocheck.checks.base import Check
from reprocheck.checks.base import run_checks as _run_checks
from reprocheck.checks.basic import (
    RC001_NO_README,
    RC002_NO_PYTHON_VERSION,
    RC003_NO_PYTHON_CONFIG,
    RC004_NOT_A_GIT_REPOSITORY,
    RC005_DIRTY_WORKING_TREE,
    RC006_NO_TESTS,
    RC007_NO_CI_WORKFLOWS,
    RC008_NO_GITIGNORE,
    RC009_MULTIPLE_PACKAGE_MANAGERS,
    RC010_NO_PYTHON_FILES,
    check_git,
    check_gitignore,
    check_package_managers,
    check_python_config,
    check_python_files,
    check_python_version,
    check_readme,
    check_tests,
    check_workflows,
)
from reprocheck.checks.gitignore import (
    RC140_ARTIFACTS_NOT_IGNORED,
    check_gitignore_artifacts,
)
from reprocheck.checks.package_managers import (
    RC110_MULTIPLE_LOCKFILES,
    RC111_LOCKFILE_WITHOUT_CONFIG,
    RC112_DOCUMENTED_TOOL_NOT_CONFIGURED,
    RC113_ORPHAN_LOCKFILE,
    RC114_DOCUMENTED_INSTALL_IGNORES_LOCKFILE,
    RC115_UNDECLARED_DOCUMENTED_INSTALL,
    RC116_CONFIGURED_TOOL_WITHOUT_LOCKFILE,
    check_package_manager_roles,
)
from reprocheck.checks.paths import (
    RC120_ABSOLUTE_PATH,
    RC121_MISSING_LOCAL_PATH,
    check_absolute_paths,
    check_missing_local_paths,
)
from reprocheck.checks.python_versions import (
    RC100_CONSISTENT,
    RC101_LOCAL_CONFLICT,
    RC102_CI_CONFLICT,
    RC103_NO_OVERLAP,
    RC104_UNPARSABLE,
    check_python_versions,
)
from reprocheck.checks.readme_refs import (
    RC130_MISSING_REQUIREMENTS,
    RC131_MISSING_SCRIPT,
    RC132_MISSING_DIRECTORY,
    check_readme_references,
)
from reprocheck.facts import Facts
from reprocheck.models import Finding

#: Every check, in report order.
ALL_CHECKS: tuple[Check, ...] = (
    # V0.1 objective facts
    check_readme,
    check_python_version,
    check_python_config,
    check_git,
    check_tests,
    check_workflows,
    check_gitignore,
    check_package_managers,
    check_python_files,
    # V0.2 consistency rules
    check_python_versions,
    check_package_manager_roles,
    check_readme_references,
    check_absolute_paths,
    check_missing_local_paths,
    check_gitignore_artifacts,
)


def run_checks(facts: Facts, checks: tuple[Check, ...] = ALL_CHECKS) -> list[Finding]:
    """Apply every check to ``facts`` and return the findings."""
    return _run_checks(checks, facts)


__all__ = [
    "ALL_CHECKS",
    "RC001_NO_README",
    "RC002_NO_PYTHON_VERSION",
    "RC003_NO_PYTHON_CONFIG",
    "RC004_NOT_A_GIT_REPOSITORY",
    "RC005_DIRTY_WORKING_TREE",
    "RC006_NO_TESTS",
    "RC007_NO_CI_WORKFLOWS",
    "RC008_NO_GITIGNORE",
    "RC009_MULTIPLE_PACKAGE_MANAGERS",
    "RC010_NO_PYTHON_FILES",
    "RC100_CONSISTENT",
    "RC101_LOCAL_CONFLICT",
    "RC102_CI_CONFLICT",
    "RC103_NO_OVERLAP",
    "RC104_UNPARSABLE",
    "RC110_MULTIPLE_LOCKFILES",
    "RC111_LOCKFILE_WITHOUT_CONFIG",
    "RC112_DOCUMENTED_TOOL_NOT_CONFIGURED",
    "RC113_ORPHAN_LOCKFILE",
    "RC114_DOCUMENTED_INSTALL_IGNORES_LOCKFILE",
    "RC115_UNDECLARED_DOCUMENTED_INSTALL",
    "RC116_CONFIGURED_TOOL_WITHOUT_LOCKFILE",
    "RC120_ABSOLUTE_PATH",
    "RC121_MISSING_LOCAL_PATH",
    "RC130_MISSING_REQUIREMENTS",
    "RC131_MISSING_SCRIPT",
    "RC132_MISSING_DIRECTORY",
    "RC140_ARTIFACTS_NOT_IGNORED",
    "Check",
    "Facts",
    "Finding",
    "run_checks",
]
