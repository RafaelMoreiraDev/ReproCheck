"""Shared fixtures: temporary projects and filesystem snapshots."""

from __future__ import annotations

import base64
import hashlib
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
