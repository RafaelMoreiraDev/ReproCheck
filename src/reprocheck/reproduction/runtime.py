"""Optional runtime checks inside the reproduced environment.

Everything in this module **executes project code**: an import runs the
module, and pytest collection runs ``conftest.py``, plugins and test imports.
That is why none of it runs unless ``--runtime-checks`` is passed.

The import target is never guessed from the distribution name. It is read from
the installed distribution itself, through ``importlib.metadata``, and only
candidates that are valid Python identifiers and are not packaging or
documentation directories are considered.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from reprocheck.reproduction.installer import venv_python
from reprocheck.reproduction.models import (
    ImportCheck,
    InstalledDistribution,
    TestCollection,
    snippet,
)
from reprocheck.reproduction.workspace import (
    TIMEOUT_EXIT_CODE,
    Workspace,
    read_log,
    run_command,
)

#: One import must not take longer than this.
IMPORT_TIMEOUT = 30

COLLECT_TIMEOUT = 300

DISCOVERY_TIMEOUT = 60

#: Top-level names that are packaging data or documentation, not a package.
_NON_PACKAGE_TOPLEVEL = frozenset(
    {
        "__pycache__",
        "bin",
        "data",
        "doc",
        "docs",
        "etc",
        "example",
        "examples",
        "include",
        "lib",
        "locale",
        "man",
        "sample",
        "samples",
        "script",
        "scripts",
        "share",
        "test",
        "tests",
        "testing",
        "var",
    }
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COLLECTED_RE = re.compile(r"(\d+)\s+tests? collected", re.IGNORECASE)

# Versions a build backend falls back to when it cannot compute a real one.
_FALLBACK_VERSIONS = frozenset({"0.0.0", "0.0.1", "0.1.dev0", "0.0.0.post0"})

# Prints the top-level modules that belong to an installed distribution.
# Packaging data and invalid identifiers are rejected here; the documentation
# and test directories are filtered by the caller, which owns that list.
_DISCOVERY_SCRIPT = """
import json, keyword, sys
from importlib.metadata import PackageNotFoundError, distribution

name = sys.argv[1]
SKIP_SUFFIXES = (".dist-info", ".egg-info", ".data")

try:
    dist = distribution(name)
except PackageNotFoundError:
    print(json.dumps({"found": False, "reason": "distribution not installed"}))
    raise SystemExit(0)

tops = set()
for entry in dist.files or []:
    parts = str(entry).replace("\\\\", "/").split("/")
    top = parts[0] if parts else ""
    if not top or top.endswith(SKIP_SUFFIXES):
        continue
    is_module = top.endswith(".py") and len(parts) == 1
    is_package = len(parts) > 1 and not parts[-1].endswith(".dist-info")
    if not (is_module or is_package):
        continue
    name_part = top[:-3] if is_module else top
    if not name_part.isidentifier() or keyword.iskeyword(name_part):
        continue
    tops.add(name_part)

print(json.dumps({"found": True, "candidates": sorted(tops)}))
"""

# Prints the installed version of a distribution.
_VERSION_SCRIPT = """
import json, sys
from importlib.metadata import PackageNotFoundError, version

name = sys.argv[1]
try:
    print(json.dumps({"version": version(name)}))
except PackageNotFoundError:
    print(json.dumps({"version": None, "reason": "distribution not installed"}))
"""

# Reports whether pytest is importable in this environment.
_PYTEST_PROBE_SCRIPT = """
import importlib.util, json

print(json.dumps({"available": importlib.util.find_spec("pytest") is not None}))
"""

_COLLECTED_RE = re.compile(r"(\d+)\s+tests? collected", re.IGNORECASE)


def read_installed_version(
    workspace: Workspace, name: str, env: dict[str, str]
) -> InstalledDistribution:
    """Read ``importlib.metadata.version(name)`` from the reproduced venv."""
    result = run_command(
        [venv_python(workspace), "-c", _VERSION_SCRIPT, name],
        cwd=workspace.source,
        logs=workspace.logs,
        name="installed-version",
        env=env,
        timeout=DISCOVERY_TIMEOUT,
    )
    if not result.succeeded:
        return InstalledDistribution(
            name=name, note=f"could not read the version (exit {result.exit_code})"
        )
    data = _read_json(result.stdout_path, result.stderr_path)
    version = data.get("version")
    if not isinstance(version, str) or not version:
        return InstalledDistribution(name=name, note=data.get("reason"))
    return InstalledDistribution(
        name=name,
        version=version,
        looks_like_fallback=version in _FALLBACK_VERSIONS,
        note=(
            "looks like a fallback version a build backend produces when it "
            "cannot compute one"
            if version in _FALLBACK_VERSIONS
            else None
        ),
    )


def discover_import_targets(
    workspace: Workspace, name: str, env: dict[str, str]
) -> tuple[tuple[str, ...], str | None]:
    """Discover the top-level modules of the installed distribution.

    Returns the candidates and a reason when the target is not determinable.
    """
    if not name:
        return ((), "no distribution name is declared")
    result = run_command(
        [venv_python(workspace), "-c", _DISCOVERY_SCRIPT, name],
        cwd=workspace.source,
        logs=workspace.logs,
        name="import-discovery",
        env=env,
        timeout=DISCOVERY_TIMEOUT,
    )
    if not result.succeeded:
        return ((), f"discovery failed (exit {result.exit_code})")
    data = _read_json(result.stdout_path, result.stderr_path)
    if not data.get("found"):
        return ((), str(data.get("reason") or "distribution not found"))
    candidates = tuple(
        item
        for item in data.get("candidates", [])
        if isinstance(item, str)
        and _IDENTIFIER_RE.match(item)
        and item not in _NON_PACKAGE_TOPLEVEL
    )
    if not candidates:
        return ((), "no unambiguous top-level module in the installed files")
    return (candidates, None)


def run_import_checks(
    workspace: Workspace, candidates: tuple[str, ...], env: dict[str, str]
) -> tuple[ImportCheck, ...]:
    """Import every candidate, one process each, with a short timeout."""
    checks: list[ImportCheck] = []
    for module in candidates:
        result = run_command(
            [venv_python(workspace), "-c", f"import {module}"],
            cwd=workspace.source,
            logs=workspace.logs,
            name=f"import-{module}",
            env=env,
            timeout=IMPORT_TIMEOUT,
        )
        checks.append(
            ImportCheck(
                module=module,
                imported=result.succeeded,
                exit_code=result.exit_code,
                duration_seconds=result.duration_seconds,
                timed_out=result.exit_code == TIMEOUT_EXIT_CODE,
                stderr_snippet=result.stderr_snippet,
                stdout_path=result.stdout_path,
                stderr_path=result.stderr_path,
                error=None if result.succeeded else snippet(result.stderr_snippet, 400),
            )
        )
    return tuple(checks)


def collect_tests(workspace: Workspace, env: dict[str, str]) -> TestCollection:
    """Run ``pytest --collect-only`` when pytest exists in the venv.

    Tests are never executed: only the collection step runs.
    """
    probe = run_command(
        [venv_python(workspace), "-c", _PYTEST_PROBE_SCRIPT],
        cwd=workspace.source,
        logs=workspace.logs,
        name="pytest-probe",
        env=env,
        timeout=DISCOVERY_TIMEOUT,
    )
    available = bool(_read_json(probe.stdout_path, probe.stderr_path).get("available"))
    if not available:
        return TestCollection(
            available=False,
            reason="pytest is not installed in the reproduced environment",
        )

    result = run_command(
        [
            venv_python(workspace),
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--no-header",
        ],
        cwd=workspace.source,
        logs=workspace.logs,
        name="pytest-collect",
        env=env,
        timeout=COLLECT_TIMEOUT,
    )
    output = read_log(result.stdout_path) + read_log(result.stderr_path)
    return TestCollection(
        available=True,
        ran=True,
        success=result.succeeded,
        exit_code=result.exit_code,
        collected=_parse_collected(output),
        duration_seconds=result.duration_seconds,
        stdout_snippet=result.stdout_snippet,
        stderr_snippet=result.stderr_snippet,
        stdout_path=result.stdout_path,
        stderr_path=result.stderr_path,
        reason=None if result.succeeded else "collection failed",
    )


def _parse_collected(output: str) -> int | None:
    match = _COLLECTED_RE.search(output)
    return int(match.group(1)) if match else None


def _read_json(*paths: str | None) -> dict:
    for path in paths:
        if not path:
            continue
        text = read_log(Path(path))
        if not text:
            continue
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    return parsed
    return {}
