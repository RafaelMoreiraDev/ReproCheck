"""Scan orchestration: collect facts, then apply the deterministic checks.

The scan is read-only: nothing inside the analysed project is created,
modified, deleted or executed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from reprocheck import __version__
from reprocheck.checks import ALL_CHECKS, run_checks
from reprocheck.facts import (
    KIND_BUILD,
    KIND_CONSTRAINT,
    DependencyDeclaration,
    Facts,
)
from reprocheck.models import ScanReport
from reprocheck.scanners import (
    dependencies,
    files,
    git,
    gitignore,
    packaging,
    paths,
    python_env,
    readme,
    tools,
)
from reprocheck.scanners.dependencies import DependencyScan
from reprocheck.scanners.project import scan_project

#: Bumped to "2" in V0.3, when the `dependencies` section was added.
REPORT_SCHEMA_VERSION = "2"


class ScanError(Exception):
    """Raised when the requested path cannot be scanned."""


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp with second precision."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def scan(path: str | Path) -> ScanReport:
    """Scan ``path`` in read-only mode and return a :class:`ScanReport`."""
    root = _resolve_target(path)
    facts = collect_facts(root)

    return ScanReport(
        reprocheck_version=__version__,
        scan_timestamp=utc_timestamp(),
        project=facts.project,
        git=facts.git,
        detected_files=facts.detected_files,
        python_requirements=facts.python_requirements,
        package_manager_hints=facts.package_manager_hints,
        readme_commands=facts.readme_commands,
        findings=run_checks(facts, ALL_CHECKS),
        report_schema_version=REPORT_SCHEMA_VERSION,
        facts=facts.to_dict(),
        dependencies=_dependency_section(facts),
    )


def _dependency_section(facts: Facts) -> dict[str, object]:
    declarations = [
        item.to_dict()
        for item in sorted(
            facts.dependency_declarations,
            key=lambda item: (
                item.name,
                item.kind,
                item.group,
                item.specifier,
                item.file or "",
                item.line or 0,
            ),
        )
    ]
    includes = [
        item.to_dict()
        for item in sorted(
            facts.requirement_includes,
            key=lambda item: (item.file, item.line, item.include_kind, item.value),
        )
    ]
    return {
        "declarations": declarations,
        "includes": includes,
        "summary": DependencyScan(
            declarations=facts.dependency_declarations, includes=[]
        ).summary,
    }


def collect_facts(root: Path) -> Facts:
    """Run every read-only scanner and return the observed facts."""
    absolute_paths, file_references = paths.scan_paths(root)
    metadata = packaging.scan_project_metadata(root)
    dependency_scan = dependencies.scan_dependencies(root)
    return Facts(
        project=scan_project(root),
        git=git.scan_git(root),
        detected_files=files.scan_files(root),
        python_requirements=python_env.scan_python_requirements(root),
        package_manager_hints=packaging.scan_package_manager_hints(root),
        package_manager_signals=packaging.scan_package_manager_signals(root),
        readme_commands=readme.scan_readme_commands(root),
        readme_references=readme.scan_readme_references(root),
        absolute_paths=absolute_paths,
        file_references=file_references,
        tools=tools.scan_tools(root),
        distribution_name=metadata.name,
        declared_dependencies=_declared_names(dependency_scan.declarations),
        dependency_declarations=dependency_scan.declarations,
        requirement_includes=dependency_scan.includes,
        gitignore=gitignore.scan_gitignore(root),
    )


def _declared_names(declarations: list[DependencyDeclaration]) -> tuple[str, ...]:
    """Names the project declares as installable requirements.

    Build requirements and constraint files are excluded: they do not describe
    what the project needs to run.
    """
    return tuple(
        sorted(
            {
                item.name
                for item in declarations
                if item.name and item.kind not in {KIND_BUILD, KIND_CONSTRAINT}
            }
        )
    )


def _resolve_target(path: str | Path) -> Path:
    root = Path(path).expanduser()
    if not root.exists():
        raise ScanError(f"path does not exist: {root}")
    if not root.is_dir():
        raise ScanError(f"path is not a directory: {root}")
    return root.resolve()
