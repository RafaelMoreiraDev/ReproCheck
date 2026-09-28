"""Shared fixtures: temporary projects and filesystem snapshots."""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

GIT_AVAILABLE = shutil.which("git") is not None
requires_git = pytest.mark.skipif(not GIT_AVAILABLE, reason="git is not installed")

ProjectFactory = Callable[..., Path]


def _write(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path: Path) -> None:
    """Keep every default destination out of the real user state directory.

    Since V0.8 the CLI writes reports to ``REPROCHECK_STATE_DIR`` when no
    destination is given. Pointing it at the pytest temp directory means a test
    that forgets ``--markdown`` cannot leave anything in the user's profile.
    """
    previous = os.environ.get("REPROCHECK_STATE_DIR")
    os.environ["REPROCHECK_STATE_DIR"] = str(tmp_path / "state")
    yield
    if previous is None:
        os.environ.pop("REPROCHECK_STATE_DIR", None)
    else:
        os.environ["REPROCHECK_STATE_DIR"] = previous


@pytest.fixture
def make_project(tmp_path: Path) -> ProjectFactory:
    """Return a factory that materialises a project tree from a file mapping."""

    def factory(files: dict[str, str] | None = None, name: str = "example") -> Path:
        root = tmp_path / name
        root.mkdir(parents=True, exist_ok=True)
        if files:
            _write(root, files)
        return root

    return factory


@pytest.fixture
def git_project(make_project: ProjectFactory) -> ProjectFactory:
    """Same as ``make_project`` but initialised as a Git repository."""

    def factory(files: dict[str, str] | None = None, name: str = "example") -> Path:
        root = make_project(files, name=name)
        run_git(root, "init", "-b", "main")
        run_git(root, "config", "user.email", "test@example.com")
        run_git(root, "config", "user.name", "ReproCheck Test")
        run_git(root, "add", "-A")
        run_git(root, "commit", "-m", "initial")
        return root

    return factory


@pytest.fixture
def finding_ids(make_project: ProjectFactory) -> Callable[[dict[str, str]], set[str]]:
    """Return a helper that scans a temporary project and yields finding IDs."""
    from reprocheck.scanner import scan

    def helper(files: dict[str, str], name: str = "example") -> set[str]:
        root = make_project(files, name=name)
        return {finding.id for finding in scan(root).findings}

    return helper


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a Git command inside ``root`` (test setup only)."""
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def snapshot(root: Path, exclude: Iterable[str] = ()) -> dict[str, tuple[int, str]]:
    """Map every file under ``root`` to ``(size, sha256)``."""
    excluded = {".git"}
    excluded.update(exclude)
    result: dict[str, tuple[int, str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.split("/")[0] in excluded:
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        result[relative] = (len(data), digest)
    return result


# --------------------------------------------------------------------------- #
# Offline installation fixtures
#
# No test in this suite reaches the network. Two building blocks make that
# possible: a project with an in-tree PEP 517 backend (so ``pip install
# <source>`` needs no build dependency at all) and a hand-written wheel (so a
# requirement can be satisfied from a local directory).
# --------------------------------------------------------------------------- #

_BACKEND_TEMPLATE = '''"""Minimal in-tree PEP 517 backend used by the ReproCheck test suite."""

import base64
import hashlib
import os
import zipfile

DIST = "{distribution}"
VERSION = "{version}"
REQUIRES = {requires!r}


def _metadata() -> str:
    lines = [
        "Metadata-Version: 2.1",
        "Name: " + "{name}",
        "Version: " + VERSION,
    ]
    for requirement in REQUIRES:
        lines.append("Requires-Dist: " + requirement)
    return "\\n".join(lines) + "\\n"


def _payload():
    """Every top-level module and package of the project, as wheel entries."""
    root = os.path.dirname(os.path.abspath(__file__))
    excluded = {{"inhouse_backend.py", "setup.py", "conftest.py"}}
    entries = []
    for name in sorted(os.listdir(root)):
        if name in excluded or name.startswith("."):
            continue
        path = os.path.join(root, name)
        if name.endswith(".py"):
            with open(path, "rb") as handle:
                entries.append((name, handle.read()))
        elif os.path.isfile(os.path.join(path, "__init__.py")):
            for member in sorted(os.listdir(path)):
                if not member.endswith(".py"):
                    continue
                with open(os.path.join(path, member), "rb") as handle:
                    entries.append((name + "/" + member, handle.read()))
    if not entries:
        entries.append((DIST + "/__init__.py", b"VALUE = 1\\n"))
    return entries


def get_requires_for_build_wheel(config_settings=None):
    return []


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    name = "{{}}-{{}}-py3-none-any.whl".format(DIST, VERSION)
    path = os.path.join(wheel_directory, name)
    dist_info = "{{}}-{{}}.dist-info".format(DIST, VERSION)
    entries = _payload() + [
        ("{{}}/METADATA".format(dist_info), _metadata().encode()),
        (
            "{{}}/WHEEL".format(dist_info),
            b"Wheel-Version: 1.0\\nGenerator: reprocheck-tests\\n"
            b"Root-Is-Purelib: true\\nTag: py3-none-any\\n",
        ),
    ]
    records = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for arcname, data in entries:
            archive.writestr(arcname, data)
            digest = (
                base64.urlsafe_b64encode(hashlib.sha256(data).digest())
                .rstrip(b"=")
                .decode()
            )
            records.append("{{}},sha256={{}},{{}}".format(arcname, digest, len(data)))
        records.append("{{}}/RECORD,,".format(dist_info))
        archive.writestr(
            "{{}}/RECORD".format(dist_info), "\\n".join(records) + "\\n"
        )
    return name
'''


def inhouse_backend_files(
    name: str,
    version: str = "0.1.0",
    requires: list[str] | None = None,
    requires_python: str | None = None,
) -> dict[str, str]:
    """Files for a project that builds itself with an in-tree backend.

    ``requires_python`` defaults to the interpreter running the tests, so the
    deterministic Python selection always has at least one candidate.
    """
    distribution = name.replace("-", "_")
    python_requirement = requires_python or (
        f">={sys.version_info[0]}.{sys.version_info[1]}"
    )
    return {
        "pyproject.toml": (
            "[build-system]\n"
            "requires = []\n"
            'build-backend = "inhouse_backend"\n'
            'backend-path = ["."]\n'
            "\n"
            "[project]\n"
            f'name = "{name}"\n'
            f'version = "{version}"\n'
            f'requires-python = "{python_requirement}"\n'
        ),
        "inhouse_backend.py": _BACKEND_TEMPLATE.format(
            name=name,
            distribution=distribution,
            version=version,
            requires=requires or [],
        ),
        f"{distribution}.py": "VALUE = 1\n",
    }


def make_wheel(
    directory,
    name: str,
    version: str = "1.0",
    requires: list[str] | None = None,
):
    """Write a minimal but valid wheel into ``directory``."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    distribution = name.replace("-", "_")
    dist_info = f"{distribution}-{version}.dist-info"
    wheel = directory / f"{distribution}-{version}-py3-none-any.whl"
    metadata = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
    ]
    metadata += [f"Requires-Dist: {item}" for item in requires or []]
    entries = {
        f"{distribution}/__init__.py": b"VALUE = 1\n",
        f"{dist_info}/METADATA": ("\n".join(metadata) + "\n").encode(),
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: reprocheck-tests\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    records = []
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for arcname, data in entries.items():
            archive.writestr(arcname, data)
            digest = (
                base64.urlsafe_b64encode(hashlib.sha256(data).digest())
                .rstrip(b"=")
                .decode()
            )
            records.append(f"{arcname},sha256={digest},{len(data)}")
        records.append(f"{dist_info}/RECORD,,")
        archive.writestr(f"{dist_info}/RECORD", "\n".join(records) + "\n")
    return wheel


# --------------------------------------------------------------------------- #
# Report documents
#
# Report *documents* (the JSON shape) rather than report objects, because the
# comparison engine only ever sees what was written to disk. The defaults are
# the "clean project" state: no findings, one dependency, one Python
# declaration, one CI reference and a successful reproduction.
# --------------------------------------------------------------------------- #


def report_document(
    *,
    timestamp: str = "2026-01-01T00:00:00+00:00",
    reprocheck_version: str = "0.8.0",
    schema: str = "6",
    verdict: str = "NOT_ATTEMPTED",
    findings: list[dict] | None = None,
    declarations: list[dict] | None = None,
    python_requirements: list[dict] | None = None,
    workflow_references: list[dict] | None = None,
    reproduction: dict | None = None,
) -> dict:
    """Return a report document shaped exactly like ``ScanReport.to_dict``."""
    document: dict = {
        "reprocheck_version": reprocheck_version,
        "report_schema_version": schema,
        "scan_timestamp": timestamp,
        "project": {
            "name": "demo",
            "path": "C:/tmp/demo",
            "python_file_count": 4,
        },
        "git": {
            "is_repository": True,
            "branch": "main",
            "head": "0123456789abcdef0123456789abcdef01234567",
            "is_clean": True,
        },
        "detected_files": [],
        "python_requirements": (
            python_requirements
            if python_requirements is not None
            else [
                {
                    "source": ".python-version",
                    "value": "3.11",
                    "file": ".python-version",
                    "line": 1,
                }
            ]
        ),
        "package_manager_hints": [],
        "readme_commands": [],
        "findings": list(findings or []),
        "dependencies": {
            "declarations": (
                declarations
                if declarations is not None
                else [dependency_declaration("numpy", "==1.23.5")]
            ),
            "includes": [],
            "summary": {
                "runtime": 1,
                "dev": 0,
                "test": 0,
                "optional": 0,
                "build": 0,
                "constraint": 0,
                "unique_packages": 1,
            },
        },
        "workflow_references": (
            workflow_references
            if workflow_references is not None
            else [
                workflow_reference(
                    "actions/checkout", "v4", ".github/workflows/ci.yml", 12
                )
            ]
        ),
        "verdict": {
            "status": verdict,
            "reasons": [] if verdict == "PASS" else ["synthetic"],
            "not_verified": [
                {"item": "full test suite", "reason": "never executed"},
            ],
        },
        "facts": {},
    }
    if reproduction is not None:
        document["reproduction"] = reproduction
    return document


def dependency_declaration(
    name: str,
    specifier: str = "",
    *,
    group: str = "project",
    kind: str = "runtime",
    reference: str | None = None,
    reference_kind: str | None = None,
    vcs_ref: str | None = None,
    vcs_commit: str | None = None,
    file: str = "pyproject.toml",
    line: int | None = 7,
) -> dict:
    """One entry of ``dependencies.declarations``."""
    return {
        "name": name,
        "raw_name": name,
        "specifier": specifier,
        "source": "pyproject",
        "group": group,
        "kind": kind,
        "marker": None,
        "extras": [],
        "file": file,
        "line": line,
        "raw": f"{name}{specifier}",
        "reference": reference,
        "reference_kind": reference_kind,
        "vcs_ref": vcs_ref,
        "vcs_commit": vcs_commit,
    }


def workflow_reference(
    target: str,
    ref: str,
    file: str = ".github/workflows/ci.yml",
    line: int = 12,
    *,
    is_local: bool = False,
    is_sha: bool = False,
) -> dict:
    """One entry of ``workflow_references``."""
    return {
        "file": file,
        "line": line,
        "raw": f"{target}@{ref}",
        "target": target,
        "ref": ref,
        "reference_type": "action",
        "job": "build",
        "step": None,
        "is_local": is_local,
        "is_sha": is_sha,
        "is_mutable": not (is_local or is_sha),
    }


def finding(
    ident: str = "RC203",
    message: str = "'requests' is declared without a version bound.",
    *,
    evidence: str = "requests",
    severity: str = "info",
    file: str = "pyproject.toml",
    line: int | None = 7,
    title: str = "Runtime dependency without a version constraint",
) -> dict:
    """One entry of ``findings``."""
    return {
        "id": ident,
        "title": title,
        "severity": severity,
        "category": "dependencies",
        "message": message,
        "evidence": evidence,
        "file": file,
        "line": line,
        "confidence": "high",
    }


def reproduction_document(
    *,
    python: str = "3.11.16",
    strategy: str = "project",
    install_success: bool = True,
    pip_clean: bool = True,
    conflicts: int = 0,
    version: str = "1.2.3",
    fallback: bool = False,
    modules: tuple[str, ...] = ("api",),
    failing: tuple[str, ...] = (),
    runtime_checks: bool = True,
    collection_available: bool = False,
    collection_collected: int | None = None,
    workspace: str = "C:/Temp/reprocheck/20260101-000000-abcdef01",
    duration: float = 12.5,
    unchanged: bool = True,
) -> dict:
    """A ``reproduction`` section shaped like ``ReproductionReport.to_dict``."""
    return {
        "attempted": True,
        "workspace": None,
        "workspace_kept": False,
        "source": f"{workspace}/source",
        "original_project": "C:/tmp/demo",
        "network_enabled": True,
        "runtime_checks_enabled": runtime_checks,
        "python": {
            "selected": python,
            "executable": "C:/Python311/python.exe",
            "reason": "lowest installed version satisfying >=3.11",
        },
        "venv": {
            "path": f"{workspace}/venv",
            "python": f"{workspace}/venv/Scripts/python.exe",
            "python_version": python,
            "initial_pip_version": "pip 24.0",
            "created": True,
            "duration_seconds": 3.2,
        },
        "installation": {
            "strategy": strategy,
            "command": ["python", "-m", "pip", "install", "C:/tmp/demo"],
            "cwd": f"{workspace}/source",
            "source": f"{workspace}/source",
            "venv": f"{workspace}/venv",
            "network_enabled": True,
            "exit_code": 0 if install_success else 1,
            "duration_seconds": duration,
            "success": install_success,
            "stdout_path": f"{workspace}/logs/install.stdout.log",
            "stderr_path": f"{workspace}/logs/install.stderr.log",
            "stdout_snippet": "Successfully installed demo-1.2.3",
            "stderr_snippet": "",
        },
        "pip_check": {
            "ran": True,
            "clean": pip_clean,
            "exit_code": 0 if pip_clean else 1,
            "conflict_count": conflicts,
            "conflicts": ["alpha requires sharedlib==1.0, but 2.0 is installed"][
                :conflicts
            ],
            "stdout_path": f"{workspace}/logs/pip-check.stdout.log",
        },
        "installed_distribution": {
            "name": "demo",
            "version": version,
            "looks_like_fallback": fallback,
            "note": None,
        },
        "runtime_checks": {
            "enabled": runtime_checks,
            "import_discovery": "distribution 'demo' provides 1 top-level module",
            "imports": [
                {
                    "module": module,
                    "imported": module not in failing,
                    "exit_code": 0 if module not in failing else 1,
                    "timed_out": False,
                    "duration_seconds": 0.4,
                    "stderr_snippet": "",
                    "stderr_path": f"{workspace}/logs/import-{module}.log",
                }
                for module in modules
            ],
            "pytest_collection": {
                "available": collection_available,
                "ran": collection_available,
                "success": True if collection_available else None,
                "exit_code": 0 if collection_available else None,
                "collected": collection_collected,
                "duration_seconds": 1.1 if collection_available else None,
                "reason": (
                    None
                    if collection_available
                    else "pytest is not installed in the reproduced environment"
                ),
            },
        },
        "original_project_unchanged": unchanged,
        "integrity": {
            "captured_before": True,
            "unchanged": unchanged,
            "git_head_before": "0123456789abcdef0123456789abcdef01234567",
            "git_head_after": "0123456789abcdef0123456789abcdef01234567",
            "tracked_files": 42,
            "changed_paths": [] if unchanged else ["data/out.csv"],
        },
        "completed_steps": [
            "static scan",
            "workspace created",
            "python selected: 3.11.16",
            "installation succeeded",
        ],
    }
