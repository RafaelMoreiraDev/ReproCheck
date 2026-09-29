"""Conda-compatible environment managers, behind one small adapter.

Three programs solve the same problem with almost the same command line, and
only the executable's name and a couple of flags differ. Rather than three code
paths, each manager is a table of strings and every step is built by one
function.

The preference order is ``micromamba > mamba > conda``, and it is a decision
rather than a coincidence: micromamba is a single static binary, mamba is the
C++ solver, and conda is the reference implementation. When more than one is
installed the choice is still deterministic and is recorded in the report, so
the reader can see which one actually ran.

**No manager is ever installed.** If none is found the attempt stops with
RC600 and the report says so. Downloading a package manager to make a
reproduction succeed would be a larger action than the reproduction itself.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from reprocheck.reproduction.models import CommandResult, snippet
from reprocheck.reproduction.workspace import Workspace, run_command

VERSION_TIMEOUT = 120
CREATE_TIMEOUT = 3600
LIST_TIMEOUT = 300
PROBE_TIMEOUT = 120

#: Longest output a manager's version string is parsed from.
_VERSION_CHARS = 400


@dataclass(frozen=True, slots=True)
class ManagerSpec:
    """Everything that differs between the three supported managers."""

    #: The executable as it appears on ``PATH``.
    name: str
    #: The literal tokens that create an environment, before any flag.
    #: ``("env", "create")`` for conda and mamba, ``("create",)`` for micromamba.
    create_tokens: tuple[str, ...]
    #: Subcommand that lists the packages of an environment.
    list_subcommand: str
    #: Flag that forces JSON output from the list subcommand.
    json_flag: str = "--json"
    #: True when the manager accepts a prefix path directly.
    accepts_prefix: bool = True
    note: str = ""


#: Ordered by preference. The order is part of the contract and is tested.
MANAGERS: tuple[ManagerSpec, ...] = (
    ManagerSpec(
        name="micromamba",
        create_tokens=("create",),
        list_subcommand="list",
        note="a single static binary; the fastest of the three",
    ),
    ManagerSpec(
        name="mamba",
        create_tokens=("env", "create"),
        list_subcommand="list",
        note="the C++ solver, same interface as conda",
    ),
    ManagerSpec(
        name="conda",
        create_tokens=("env", "create"),
        list_subcommand="list",
        note="the reference implementation",
    ),
)

_VERSION_RE = re.compile(r"(?P<version>\d+\.\d+(?:\.\d+)?)")

#: What ``discover`` hands back: a real manager, or the object that says none
#: was found. The second is a distinct type on purpose, so the report can list
#: every candidate that was checked instead of only saying "not available".
Manager = Union["CondaManager", "_Unavailable"]


class CondaManager:
    """One manager, discovered and ready to run.

    Holds the executable and the spec; every command is built by this class so
    the three managers cannot drift apart.
    """

    def __init__(self, spec: ManagerSpec, executable: str) -> None:
        self.spec = spec
        self.executable = executable
        self.version: str | None = None
        self.discovery: list[str] = []

    # -- naming -------------------------------------------------------- #

    @property
    def name(self) -> str:
        return self.spec.name

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"CondaManager({self.name!r}, {self.executable!r})"

    # -- discovery ----------------------------------------------------- #

    @classmethod
    def discover(cls, *, explicit: str | None = None) -> Manager | None:
        """Find a manager, deterministically.

        With ``explicit``, only that manager is considered, and a name that is
        not a known manager is refused rather than guessed at. Without it, the
        preference order decides and every candidate is recorded.
        """
        if explicit:
            spec = next((item for item in MANAGERS if item.name == explicit), None)
            if spec is None:
                return None
            found = shutil.which(spec.name)
            return cls(spec, found) if found else None

        checked: list[str] = []
        for spec in MANAGERS:
            executable = shutil.which(spec.name)
            checked.append(f"{spec.name}: {executable or 'not found'}")
            if executable:
                manager = cls(spec, executable)
                manager.discovery = checked
                return manager
        unavailable = _Unavailable(checked)
        return unavailable

    def read_version(self, workspace: Workspace, env: dict[str, str]) -> str | None:
        """Ask the manager for its version and keep the number it reports.

        The candidates found during discovery are **kept**, not replaced: which
        three programs were looked for, and in what order, is the evidence for
        the choice, and overwriting it would leave the report claiming a
        preference that cannot be checked.
        """
        result = run_command(
            [self.executable, "--version"],
            cwd=workspace.root,
            logs=workspace.logs,
            name="conda-version",
            env=env,
            timeout=VERSION_TIMEOUT,
        )
        self.version = self._parse_version(result)
        self.discovery.append(
            f"{self.name} {self.version or 'version unknown'}: "
            f"version command exit code {result.exit_code}"
        )
        return self.version

    @staticmethod
    def _parse_version(result: CommandResult) -> str | None:
        text = (result.stdout_snippet or result.stderr_snippet or "")[:_VERSION_CHARS]
        match = _VERSION_RE.search(text)
        return match.group("version") if match else None

    # -- commands ------------------------------------------------------ #

    def create_command(self, *, prefix: Path, environment_file: Path) -> list[str]:
        """Build the environment creation command, without running it.

        ``conda env create`` and ``mamba env create`` take the same flags;
        ``micromamba create`` omits ``env``. Paths are passed as separate
        arguments rather than interpolated into a string, so a path containing
        a space needs no quoting at all -- which is the usual way a Windows
        reproduction breaks.
        """
        return [
            self.executable,
            *self.spec.create_tokens,
            "--prefix",
            str(prefix),
            "--file",
            str(environment_file),
            "--yes",
        ]

    def create(
        self,
        *,
        prefix: Path,
        environment_file: Path,
        workspace: Workspace,
        env: dict[str, str],
        timeout: int = CREATE_TIMEOUT,
    ) -> CommandResult:
        return run_command(
            self.create_command(prefix=prefix, environment_file=environment_file),
            cwd=workspace.root,
            logs=workspace.logs,
            name="conda-create",
            env=env,
            timeout=timeout,
        )

    def list_command(self, *, prefix: Path) -> list[str]:
        return [
            self.executable,
            self.spec.list_subcommand,
            "--prefix",
            str(prefix),
            self.spec.json_flag,
        ]

    def list_packages(
        self,
        *,
        prefix: Path,
        workspace: Workspace,
        env: dict[str, str],
        timeout: int = LIST_TIMEOUT,
    ) -> PackageListing:
        """Return the packages of an environment, parsed from JSON.

        The raw result travels with them because whether the list was
        **truncated** is a property of the output, and a caller that only sees
        the packages would have to guess.
        """
        result = run_command(
            self.list_command(prefix=prefix),
            cwd=workspace.root,
            logs=workspace.logs,
            name="conda-list",
            env=env,
            timeout=timeout,
        )
        (workspace.logs / "conda-list.json").write_text(
            read_log(result.stdout_path), encoding="utf-8"
        )
        return PackageListing(
            packages=parse_package_list(read_log(result.stdout_path)),
            result=result,
        )

    def python_path(self, prefix: Path) -> str:
        """Path of the interpreter inside a Conda prefix.

        Layout is identical on every platform Conda supports: ``bin`` on POSIX,
        ``Scripts`` on Windows.
        """
        if _is_windows():
            return str(prefix / "Scripts" / "python.exe")
        return str(prefix / "bin" / "python")


@dataclass(frozen=True, slots=True)
class PackageListing:
    """What an environment contains, and the raw result it came from."""

    packages: list[CondaPackage]
    result: CommandResult


@dataclass(frozen=True, slots=True)
class CondaPackage:
    """One entry of ``conda list --json``."""

    name: str
    version: str = ""
    build: str = ""
    channel: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "build": self.build,
            "channel": self.channel,
        }


def parse_package_list(output: str) -> list[CondaPackage]:
    """Parse ``conda list --json``, tolerating a manager that printed nothing.

    Every manager writes slightly different JSON around the same object, so
    the fields are read leniently: a missing build string or channel must not
    cost the whole list.
    """
    text = output.strip()
    if not text:
        return []
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return _parse_salvageable(text)
    if isinstance(document, dict):
        # ``conda list`` wraps in "packages"; a manager that describes exactly
        # one package may emit the object on its own. Both are accepted, because
        # dropping a real package would understate what was installed.
        if "packages" in document:
            document = document.get("packages") or []
        else:
            document = [document]
    if not isinstance(document, list):
        return []
    found: list[CondaPackage] = []
    for item in document:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        found.append(
            CondaPackage(
                name=name,
                version=str(item.get("version") or "").strip(),
                build=str(item.get("build_string") or item.get("build") or "").strip(),
                channel=str(item.get("channel") or "").strip(),
            )
        )
    return found


def _parse_salvageable(text: str) -> list[CondaPackage]:
    """Recover what a truncated JSON document still proves.

    A manager killed by the timeout can leave half a document. Reading the
    complete objects out of it is honest: each one is a package that was really
    installed. The count is then a lower bound, and the report says the parse
    was partial.
    """
    found: list[CondaPackage] = []
    for chunk in re.findall(r"\{[^{}]*\}", text):
        try:
            item = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("name"):
            found.append(
                CondaPackage(
                    name=str(item["name"]),
                    version=str(item.get("version") or ""),
                    build=str(item.get("build_string") or item.get("build") or ""),
                    channel=str(item.get("channel") or ""),
                )
            )
    return found


class _Unavailable:
    """Returned instead of a manager when none was found.

    A distinct type rather than ``None`` so the caller can report *which*
    candidates were looked for: "no conda found" is more useful when the
    report also says micromamba and mamba were checked.
    """

    def __init__(self, checked: list[str]) -> None:
        self.checked = checked
        self.name = ""
        self.executable = ""
        self.version = None
        self.discovery = list(checked)

    def __bool__(self) -> bool:
        return False

    @property
    def reason(self) -> str:
        return "no Conda-compatible environment manager is available"


def _is_windows() -> bool:
    import sys

    return sys.platform == "win32"


def read_log(path: str | Path | None) -> str:
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - defensive
        return ""


def environment_python_version(
    python: str, workspace: Workspace, env: dict[str, str]
) -> str | None:
    """Run the environment's own interpreter and read its version."""
    result = run_command(
        [python, "-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"],
        cwd=workspace.root,
        logs=workspace.logs,
        name="conda-python-version",
        env=env,
        timeout=PROBE_TIMEOUT,
    )
    return result.stdout_snippet.strip() or None if result.succeeded else None


def describe_failure(result: CommandResult, limit: int = 600) -> str:
    """A short, factual reason, never a guess about how to fix it."""
    text = result.stderr_snippet or result.stdout_snippet or ""
    return snippet(text, limit) or f"exit code {result.exit_code}"
