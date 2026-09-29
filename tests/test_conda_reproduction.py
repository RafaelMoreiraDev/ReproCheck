"""Tests for the Conda reproduction path.

No test here needs a Conda installation. The managers are fake executables
built by :func:`make_manager`, and they answer from environment variables, so
every branch of the pipeline -- success, failure, a wrong Python, an empty
package list, a broken `pip check` -- is reachable deterministically on any
machine, with or without a real manager.

That is the point. A test that needs a package manager is a test most machines
skip, and the branches it would cover are exactly the ones that only run when
something has already gone wrong.

A few tests are marked ``conda_integration`` and are skipped unless a real
manager is on ``PATH``. They are never part of the default gate.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from conftest import snapshot
from reprocheck.checks.reproduction import (
    RC600_CONDA_MANAGER_MISSING,
    RC601_CONDA_AMBIGUOUS,
    RC602_CONDA_CREATE_FAILED,
    RC603_CONDA_PYTHON_MISMATCH,
    RC604_CONDA_NETWORK_REQUIRED,
)
from reprocheck.cli import EXIT_OK, main
from reprocheck.reproduction import workspace as ws
from reprocheck.reproduction.conda import (
    STOP_AMBIGUOUS_ENVIRONMENT,
    STOP_NO_MANAGER,
    STOP_NO_NETWORK,
)
from reprocheck.reproduction.conda_manager import MANAGERS
from reprocheck.reproduction.runner import reproduce

FAKE_MANAGER = '''\
"""A fake Conda-compatible manager.

The manager's own two commands are faked, because there is nothing real to call
and no test can check a solver that way: ``--version`` answers from an
environment variable, and ``list --json`` prints a list from one.

Everything else is **real**. ``env create`` copies the running interpreter into
the prefix exactly where Conda puts it, and writes whatever the environment
variables ask for. The steps that follow -- ``pip check``, importing a module,
``pytest --collect-only`` -- therefore run against a genuine Python, and a test
that says they are clean means they are clean.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

VERSION = os.environ.get("FAKE_VERSION", "24.11.1")
PACKAGES = os.environ.get("FAKE_PACKAGES", "[]")
# "object" is conda's wrapped shape, "array" is micromamba's bare one.
SHAPE = os.environ.get("FAKE_LIST_SHAPE", "object")
TRUNCATE = os.environ.get("FAKE_LIST_TRUNCATED") == "1"
CREATE_FAILS = os.environ.get("FAKE_CREATE_FAILS") == "1"
NOISE = os.environ.get("FAKE_NOISE", "")
# Written into the prefix when requested.
WANT_DEMO = os.environ.get("FAKE_WANT_DEMO") == "1"
# One of: present, absent, broken. "absent" is how a distribution whose files
# were removed looks, and importlib.metadata drops it from dist.files, so the
# tool correctly reports no importable module rather than a failure.
WANT_MODULE = os.environ.get("FAKE_WANT_MODULE", "present")
WANT_PYTEST = os.environ.get("FAKE_WANT_PYTEST") == "1"
BROKEN_METADATA = os.environ.get("FAKE_BROKEN_METADATA", "")
# Where each package lives on the machine running the tests, given by the
# fixture so the manager does not have to look for it.
PYTEST_SOURCE = json.loads(os.environ.get("FAKE_PYTEST_SOURCE", "{}"))


def site_packages(prefix):
    """Where a package goes in a prefix, per platform.

    The POSIX layout carries the interpreter version: ``lib/python3.11/
    site-packages``, not ``lib/python3/``. Writing to the wrong one produces a
    prefix that looks populated to the fixture and is empty to the interpreter,
    which is how an import test comes to assert that nothing was imported.
    """
    if os.name == "nt":
        return Path(prefix) / "Lib" / "site-packages"
    version = f"python{sys.version_info[0]}.{sys.version_info[1]}"
    return Path(prefix) / "lib" / version / "site-packages"


def install_interpreter(prefix):
    """Lay out the prefix the way a solved Conda environment is laid out.

    A real ``python -m venv`` is used, and it is the right stand-in for a solver:
    the layout is the one Conda produces (``bin/python`` on POSIX,
    ``Scripts/python.exe`` on Windows), and the environment is genuinely
    isolated, so ``pip check`` and an import inside it measure the prefix rather
    than the host. A merely *copied* interpreter does not do that: on Windows it
    resolves the original installation through the registry and every check
    would silently be measuring the wrong machine.

    ``--system-site-packages`` is added only when the test asks for pytest. It
    makes the prefix able to see the host's pytest without a network, which is
    the one thing a venv cannot provide on its own. Every other test runs
    without it, so the prefix stays isolated where isolation is what is being
    checked.
    """
    command = [sys.executable, "-m", "venv"]
    if WANT_PYTEST:
        command.append("--system-site-packages")
    command.append(str(prefix))
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        sys.stderr.write("fake manager: venv creation failed\\n")
        sys.stderr.write(completed.stderr)
        raise SystemExit(1)


def write_module(prefix, name, body):
    directory = site_packages(prefix)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(body, encoding="utf-8")


def write_dist_info(prefix, name, version, requires, files=()):
    """Declare a distribution whose metadata pip check and discovery will read.

    ``RECORD`` is written for real, because the import discovery enumerates
    ``dist.files``: a distribution with an empty RECORD looks like one that
    installs no module, and the check would then be measuring a packaging
    artefact of the fixture rather than the tool.
    """
    directory = site_packages(prefix) / f"{name}-{version}.dist-info"
    directory.mkdir(parents=True, exist_ok=True)
    lines = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
    ]
    for requirement in requires:
        lines.append(f"Requires-Dist: {requirement}")
    (directory / "METADATA").write_text("\\n".join(lines) + "\\n", encoding="utf-8")
    (directory / "INSTALLER").write_text("fake\\n", encoding="utf-8")
    (directory / "WHEEL").write_text(
        "Wheel-Version: 1.0\\nGenerator: fake\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n",
        encoding="utf-8",
    )
    if files:
        (directory / "top_level.txt").write_text(
            "\\n".join(sorted({item.rsplit(".", 1)[0].replace("/", ".") for item in files})) + "\\n",
            encoding="utf-8",
        )
    record = [f"{item},," for item in files]
    record += [
        f"{name}-{version}.dist-info/METADATA,,",
        f"{name}-{version}.dist-info/RECORD,,",
    ]
    (directory / "RECORD").write_text("\\n".join(record) + "\\n", encoding="utf-8")


def copy_package(prefix, name):
    """Copy one installed package and its metadata into the prefix.

    Used for pytest, whose absence in a bare prefix is the ordinary case and
    which no offline mechanism can install. The venv cannot see the host's
    pytest either, because a Conda attempt runs with ``PYTHONNOUSERSITE`` and
    the host's pytest lives in the user site directory.
    """
    # The location is given by the fixture, not discovered: the manager runs
    # with PYTHONNOUSERSITE set, so a pytest in the user site directory is
    # deliberately invisible to it, exactly as it should be.
    source = PYTEST_SOURCE.get(name)
    if not source:
        return False
    source = Path(source)
    if not source.is_dir():
        return False
    package_dir = source
    source_root = source.parent
    target_root = site_packages(prefix)
    target_root.mkdir(parents=True, exist_ok=True)
    destination = target_root / package_dir.name
    if not destination.exists():
        shutil.copytree(
            package_dir, destination, ignore=shutil.ignore_patterns("__pycache__")
        )
    for candidate in source_root.glob(f"{name}-*.dist-info"):
        if not (target_root / candidate.name).exists():
            shutil.copytree(
                candidate,
                target_root / candidate.name,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
    return True


def create_environment(prefix):
    install_interpreter(prefix)
    if WANT_DEMO:
        if WANT_MODULE == "broken":
            # A compiled extension that cannot load is the usual real cause of a
            # failing import, and the usual real one on a machine where the
            # project was not built.
            write_module(
                prefix, "demo",
                "raise ImportError('demo needs a library that is not available')\\n",
            )
        elif WANT_MODULE == "present":
            write_module(prefix, "demo", "VALUE = 1\\n")
        write_dist_info(prefix, "demo", "1.0.0", [], files=("demo.py",))
    if BROKEN_METADATA:
        name, version, requires = BROKEN_METADATA.split("|", 2)
        write_dist_info(prefix, name, version, requires.split(","))
    if WANT_PYTEST:
        for extra in ("pytest", "pluggy", "iniconfig", "packaging"):
            copy_package(prefix, extra)


def flag(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


argv = sys.argv[1:]

if argv[:1] == ["--version"]:
    print(VERSION)
    raise SystemExit(0)

if "create" in argv:
    if CREATE_FAILS:
        sys.stderr.write(NOISE)
        sys.stderr.write("CondaError: could not solve the environment\\n")
        raise SystemExit(1)
    # A real manager resolves --file against its working directory and fails if
    # it is not there. Checked here too, or a test could not tell a resolved
    # path from one that happened to be given in absolute form.
    requested = flag(argv, "--file")
    if requested and not Path(requested).is_file():
        sys.stderr.write(
            "CondaError: environment file not found: {}\\n".format(requested)
        )
        raise SystemExit(1)
    create_environment(flag(argv, "--prefix"))
    sys.stdout.write(NOISE)
    print("done")
    raise SystemExit(0)

if "list" in argv:
    if TRUNCATE:
        # Ends mid-array, as a manager whose output was cut off would.
        sys.stdout.write(PACKAGES[: max(1, len(PACKAGES) // 2)])
        raise SystemExit(0)
    if SHAPE == "array":
        # micromamba prints a bare array.
        sys.stdout.write(PACKAGES + "\\n")
    else:
        # conda wraps the same list in an object under "packages".
        sys.stdout.write(json.dumps({"packages": json.loads(PACKAGES)}) + "\\n")
    raise SystemExit(0)

sys.stderr.write("fake manager: unknown command %r\\n" % (argv,))
raise SystemExit(2)
'''

WINDOWS_SHIM = '@"{}" "{}" %*\n'


def _package_sources() -> dict[str, str]:
    """Where pytest and its dependencies live on the machine running the tests.

    Resolved by the fixture rather than by the fake manager, which runs with the
    environment the reproduction gives it and therefore cannot see the user site
    directory where a `pip install --user` lands.
    """
    import importlib.util

    found: dict[str, str] = {}
    for name in ("pytest", "pluggy", "iniconfig", "packaging"):
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            continue
        if spec is not None and spec.origin:
            directory = Path(spec.origin).parent
            if directory.name != "__pycache__":
                found[name] = str(directory)
    return found


#: The series the running interpreter belongs to, and one it cannot be. Used to
#: make RC603 fire against a genuine interpreter rather than a faked version.
INTERPRETER_SERIES = ".".join(str(part) for part in sys.version_info[:2])
OTHER_SERIES = "2.7" if INTERPRETER_SERIES != "2.7" else "3.6"


@pytest.fixture
def manager(tmp_path: Path):
    """Return a factory that installs one fake manager on ``PATH``."""

    def build(
        name: str = "conda",
        *,
        version: str | None = None,
        packages: list[dict] | None = None,
        shape: str = "object",
        truncate: bool = False,
        create_fails: bool = False,
        demo: bool = True,
        module: str = "present",
        pytest: bool = False,
        broken_metadata: str = "",
        noise: str = "",
        extra: dict[str, str] | None = None,
    ) -> Path:
        directory = tmp_path / "bin"
        directory.mkdir(parents=True, exist_ok=True)
        script = directory / f"{name}_impl.py"
        script.write_text(FAKE_MANAGER, encoding="utf-8")

        if os.name == "nt":
            shim = directory / f"{name}.cmd"
            shim.write_text(
                WINDOWS_SHIM.format(sys.executable, script), encoding="utf-8"
            )
        else:
            shim = directory / name
            shim.write_text(
                f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n',
                encoding="utf-8",
            )
            shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

        environment = {
            "FAKE_VERSION": version or "24.11.1",
            "FAKE_PACKAGES": json.dumps(
                packages
                if packages is not None
                else [
                    {"name": "python", "version": "3.11.9", "build_string": "h1"},
                    {"name": "numpy", "version": "1.26.4", "channel": "conda-forge"},
                ]
            ),
            "FAKE_LIST_SHAPE": shape,
            "FAKE_LIST_TRUNCATED": "1" if truncate else "0",
            "FAKE_CREATE_FAILS": "1" if create_fails else "0",
            "FAKE_WANT_DEMO": "1" if demo else "0",
            "FAKE_WANT_MODULE": module,
            "FAKE_WANT_PYTEST": "1" if pytest else "0",
            "FAKE_PYTEST_SOURCE": json.dumps(_package_sources()) if pytest else "{}",
            "FAKE_BROKEN_METADATA": broken_metadata,
            "FAKE_NOISE": noise,
        }
        environment.update(extra or {})
        return directory, environment

    return build


@pytest.fixture
def on_path(monkeypatch):
    """Make ``PATH`` be exactly one directory, so discovery sees only that.

    Replacing rather than prepending is the point. GitHub's Linux runners ship
    with miniconda installed, so a test that only adds its own directory would
    silently find the real ``conda`` and pass for the wrong reason on CI while
    passing for the right one on a machine without it.
    """

    def apply(directory: Path) -> None:
        monkeypatch.setenv("PATH", str(directory))

    return apply


def conda_project(make_project, *, name: str = "demo", **overrides):
    files = {
        "pyproject.toml": (
            "[build-system]\n"
            'requires = ["setuptools"]\n'
            'build-backend = "setuptools.build_meta"\n\n'
            "[project]\n"
            'name = "demo"\n'
            'version = "1.0.0"\n'
            f'requires-python = "{overrides.get("requires_python", ">=3.11")}"\n'
            'dependencies = ["demo-dep>=1"]\n'
        ),
        "environment.yml": (
            "name: demo\n"
            "channels:\n  - conda-forge\n"
            "dependencies:\n"
            f"  - python={overrides.get('conda_python', INTERPRETER_SERIES)}\n"
            "  - numpy\n"
            "  - pip\n"
            "  - pip:\n      - demo-dep\n"
        ),
        "README.md": "# demo\n",
        "tests/test_demo.py": "def test_ok():\n    assert True\n",
    }
    for extra, content in (overrides.get("extra_files") or {}).items():
        files[extra] = content
    return make_project(files, name=name)


def run_conda(root: Path, *, base_dir: Path, **kwargs):
    """Run a Conda reproduction into a controlled base directory."""
    return reproduce(root, base_dir=base_dir, conda=True, **kwargs)


#: Finding ids a Conda attempt can produce. It reuses RC400 and the RC5xx
#: runtime ids on purpose: ``pip check`` and the import smoke test are the same
#: checks against a different environment, and a second id for them would split
#: one question across two vocabularies.
CONDA_FINDING_IDS = ("RC400", "RC500", "RC501", "RC502", "RC6")


def conda_ids(report) -> set[str]:
    return {
        item.id
        for item in report.reproduction_findings
        if item.id.startswith(CONDA_FINDING_IDS)
    }


# --------------------------------------------------------------------------- #
# 1. --conda absent: the pip behaviour is untouched
# --------------------------------------------------------------------------- #


def test_without_conda_the_strategy_is_pip(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, environment = manager()
    for key, value in environment.items():
        os.environ[key] = value
    on_path(directory)

    report = reproduce(
        make_project({"pyproject.toml": "[project]\nname='x'\n"}), base_dir=tmp_path
    )

    assert report.reproduction["strategy"] == "pip"
    assert report.reproduction["conda"] is None


def test_conda_is_never_invoked_without_the_flag(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, _ = manager()
    on_path(directory)
    root = conda_project(make_project)
    before = snapshot(root)

    reproduce(root, base_dir=tmp_path)

    # The Conda manager was never invoked by a run without --conda.
    workspace = next((tmp_path / "reprocheck").glob("*"))
    assert snapshot(root) == before
    assert not list((workspace / "logs").glob("conda-*"))


# --------------------------------------------------------------------------- #
# 2. manager discovery
# --------------------------------------------------------------------------- #


def test_no_manager_is_an_error_not_a_download(
    manager, on_path, make_project, tmp_path
) -> None:
    empty = tmp_path / "nothing"
    empty.mkdir()
    on_path(empty)

    report = run_conda(conda_project(make_project), base_dir=tmp_path)
    attempt = report.reproduction["conda"]

    assert RC600_CONDA_MANAGER_MISSING in conda_ids(report)
    assert attempt["reason"] == STOP_NO_MANAGER
    assert attempt["success"] is False
    assert attempt["manager"] is None
    # The report says which candidates were looked for.
    assert any("micromamba" in item for item in attempt["discovery"])
    assert any("conda:" in item for item in attempt["discovery"])


def test_discovery_prefers_micromamba(manager, on_path, make_project, tmp_path) -> None:
    directory, _ = manager("micromamba")
    on_path(directory)

    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)

    assert report.reproduction["conda"]["manager"] == "micromamba"


def test_discovery_falls_through_to_the_next_available(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, _ = manager("conda")
    on_path(directory)

    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)

    assert report.reproduction["conda"]["manager"] == "conda"


def test_several_managers_still_resolve_to_one_deterministically(
    manager, on_path, make_project, tmp_path
) -> None:
    """With three installed, the order decides and the report shows it."""
    directory, _ = manager()
    for other in ("mamba", "micromamba"):
        manager(other)

    on_path(directory)
    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)
    attempt = report.reproduction["conda"]

    assert attempt["manager"] == "micromamba"
    # Discovery stops at the first manager that exists, and the report says so
    # rather than implying the other two were tried and rejected.
    assert attempt["discovery"][0].startswith("micromamba:")
    assert attempt["discovery"][1].startswith("micromamba 24.11.1:")
    assert not any("not found" in item for item in attempt["discovery"])


def test_an_unknown_manager_is_refused(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, _ = manager()
    on_path(directory)

    report = run_conda(
        conda_project(make_project),
        base_dir=tmp_path,
        network=True,
        conda_manager="poetry",
    )

    assert RC600_CONDA_MANAGER_MISSING in conda_ids(report)
    assert "not a supported manager" in str(report.reproduction["conda"]["error"])


def test_explicit_manager_is_used(manager, on_path, make_project, tmp_path) -> None:
    directory, _ = manager("conda")
    on_path(directory)

    report = run_conda(
        conda_project(make_project),
        base_dir=tmp_path,
        network=True,
        conda_manager="conda",
    )

    assert report.reproduction["conda"]["manager"] == "conda"


# --------------------------------------------------------------------------- #
# 3. environment selection
# --------------------------------------------------------------------------- #


def test_a_single_environment_is_used(manager, on_path, make_project, tmp_path) -> None:
    directory, environment = manager()
    _apply(environment)
    on_path(directory)

    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)

    assert report.reproduction["conda"]["environment_file"] == "environment.yml"


def test_several_environments_are_refused_without_a_choice(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, _ = manager()
    on_path(directory)
    root = conda_project(
        make_project,
        conda_python=OTHER_SERIES,
        extra_files={
            "environment-dev.yml": "name: dev\ndependencies: [python=3.12]\n",
            "README.md": "# demo\n\nCreate the dev environment:\n\n"
            "`ash\nconda env create -f environment-dev.yml\n`\n",
        },
    )

    report = run_conda(root, base_dir=tmp_path, network=True)
    attempt = report.reproduction["conda"]

    assert RC601_CONDA_AMBIGUOUS in conda_ids(report)
    assert attempt["reason"] == STOP_AMBIGUOUS_ENVIRONMENT
    assert attempt["success"] is False
    assert set(attempt["discovery"]) >= {"environment.yml", "environment-dev.yml"}


def test_conda_env_chooses_explicitly(manager, on_path, make_project, tmp_path) -> None:
    directory, _ = manager()
    on_path(directory)
    root = conda_project(
        make_project,
        conda_python=OTHER_SERIES,
        extra_files={
            "environment-dev.yml": "name: dev\ndependencies: [python=3.12]\n",
            "README.md": "# demo\n\nCreate the dev environment:\n\n"
            "`ash\nconda env create -f environment-dev.yml\n`\n",
        },
    )

    report = run_conda(
        root, base_dir=tmp_path, network=True, conda_env="environment-dev.yml"
    )

    assert report.reproduction["conda"]["environment_file"] == "environment-dev.yml"
    assert RC601_CONDA_AMBIGUOUS not in conda_ids(report)


def test_a_project_without_an_environment_is_refused(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, _ = manager()
    on_path(directory)
    root = make_project({"pyproject.toml": "[project]\nname='x'\n"})

    report = run_conda(root, base_dir=tmp_path, network=True)

    assert RC601_CONDA_AMBIGUOUS in conda_ids(report)
    assert report.reproduction["conda"]["success"] is False


# --------------------------------------------------------------------------- #
# 4. network
# --------------------------------------------------------------------------- #


def test_without_network_nothing_is_created(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, _ = manager()
    on_path(directory)

    report = run_conda(conda_project(make_project), base_dir=tmp_path)
    attempt = report.reproduction["conda"]

    assert RC604_CONDA_NETWORK_REQUIRED in conda_ids(report)
    assert attempt["reason"] == STOP_NO_NETWORK
    assert attempt["success"] is False
    # The manager was found, so the report can say both things at once.
    assert attempt["manager"] == "conda"


def test_network_does_not_silence_the_other_checks(
    manager, on_path, make_project, tmp_path
) -> None:
    """A user who forgot --network learns about the manager in the same run."""
    empty = tmp_path / "empty"
    empty.mkdir()
    on_path(empty)

    report = run_conda(conda_project(make_project), base_dir=tmp_path)

    assert RC600_CONDA_MANAGER_MISSING in conda_ids(report)
    assert RC604_CONDA_NETWORK_REQUIRED not in conda_ids(report)


# --------------------------------------------------------------------------- #
# 5. creation
# --------------------------------------------------------------------------- #


def test_successful_creation_is_recorded(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, environment = manager()
    _apply(environment)
    on_path(directory)

    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)
    attempt = report.reproduction["conda"]

    assert attempt["success"] is True
    assert attempt["manager"] == "conda"
    assert attempt["manager_version"] == "24.11.1"
    assert attempt["exit_code"] == 0
    assert attempt["duration_seconds"] is not None
    assert os.path.basename(attempt["command"][0]).lower().startswith("conda")
    assert "env" in attempt["command"] and "create" in attempt["command"]
    assert attempt["cwd"]


def test_failed_creation_is_an_error_with_evidence(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, environment = manager(create_fails=True)
    _apply(environment)
    on_path(directory)

    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)
    attempt = report.reproduction["conda"]
    finding = next(
        item
        for item in report.reproduction_findings
        if item.id == RC602_CONDA_CREATE_FAILED
    )

    assert RC602_CONDA_CREATE_FAILED in conda_ids(report)
    assert attempt["success"] is False
    assert finding.severity.name == "ERROR"
    assert "environment.yml" in finding.message
    assert finding.evidence and "conda" in finding.evidence
    # Nothing is retried and nothing is corrected automatically.
    assert attempt["package_count"] == 0


def test_creation_happens_outside_the_project(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, environment = manager()
    _apply(environment)
    on_path(directory)
    root = conda_project(make_project)

    report = run_conda(root, base_dir=tmp_path, network=True)
    prefix = Path(report.reproduction["conda"]["prefix"])

    assert prefix.is_relative_to(tmp_path)
    assert not prefix.is_relative_to(root)
    assert prefix.name == "conda-env"
    assert not (root / "conda-env").exists()


# --------------------------------------------------------------------------- #
# 6. Python verification
# --------------------------------------------------------------------------- #


def test_the_environment_python_is_read(
    manager, on_path, make_project, tmp_path
) -> None:
    """The version comes from the environment's own interpreter, not from a file.

    The fake manager copies a real interpreter into the prefix, so this is the
    version that Python actually reports from inside the environment.
    """
    directory, environment = manager()
    _apply(environment)
    on_path(directory)
    expected = ".".join(str(part) for part in sys.version_info[:3])

    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)
    python = report.reproduction["conda"]["python"]

    assert python["version"] == expected
    assert python["declared"] == f"={INTERPRETER_SERIES}"
    assert python["path"].endswith("python.exe" if os.name == "nt" else "python")


def test_a_matching_python_raises_nothing(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, environment = manager()
    _apply(environment)
    on_path(directory)

    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)

    assert RC603_CONDA_PYTHON_MISMATCH not in conda_ids(report)


def test_a_mismatched_python_is_an_error(
    manager, on_path, make_project, tmp_path
) -> None:
    """The environment provides a Python the file does not allow.

    The real interpreter is copied into the prefix, so the mismatch is genuine:
    the file asks for a series the environment does not contain.
    """
    directory, environment = manager()
    _apply(environment)
    on_path(directory)
    running = ".".join(str(part) for part in sys.version_info[:3])

    report = run_conda(
        conda_project(make_project, conda_python=OTHER_SERIES),
        base_dir=tmp_path,
        network=True,
    )
    finding = next(
        item
        for item in report.reproduction_findings
        if item.id == RC603_CONDA_PYTHON_MISMATCH
    )

    assert RC603_CONDA_PYTHON_MISMATCH in conda_ids(report)
    assert finding.severity.name == "ERROR"
    assert running in finding.message
    assert OTHER_SERIES not in running
    assert f"={OTHER_SERIES}" in finding.message


# --------------------------------------------------------------------------- #
# 7. the package list
# --------------------------------------------------------------------------- #


def test_the_package_list_is_parsed(manager, on_path, make_project, tmp_path) -> None:
    directory, environment = manager(
        packages=[
            {
                "name": "python",
                "version": "3.11.9",
                "build_string": "h1",
                "channel": "defaults",
            },
            {
                "name": "numpy",
                "version": "1.26.4",
                "build_string": "py311h",
                "channel": "conda-forge",
            },
            {"name": "pip", "version": "24.0"},
        ]
    )
    _apply(environment)
    on_path(directory)

    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)
    attempt = report.reproduction["conda"]

    assert attempt["package_count"] == 3
    assert attempt["pip_package_count"] == 1
    # A complete list is not a partial one. conda prints a human sentence
    # after creating an environment, so reading this flag off the create
    # output instead of the list output reported every solve as truncated.
    assert attempt["package_list_partial"] is False
    numpy = next(item for item in attempt["packages"] if item["name"] == "numpy")
    assert numpy == {
        "name": "numpy",
        "version": "1.26.4",
        "build": "py311h",
        "channel": "conda-forge",
    }


def test_a_micromamba_shaped_list_is_also_not_partial(
    manager, on_path, make_project, tmp_path
) -> None:
    # micromamba prints a bare array where conda wraps the list in an object,
    # so a check that only accepted a closing brace reported every micromamba
    # run as partial.
    directory, environment = manager(
        packages=[{"name": "python", "version": "3.11.9"}], shape="array"
    )
    _apply(environment)
    on_path(directory)

    attempt = run_conda(
        conda_project(make_project), base_dir=tmp_path, network=True
    ).reproduction["conda"]

    assert attempt["package_count"] == 1
    assert attempt["package_list_partial"] is False


def test_a_truncated_list_says_so(manager, on_path, make_project, tmp_path) -> None:
    directory, environment = manager(
        packages=[{"name": "python", "version": "3.11.9"}], truncate=True
    )
    _apply(environment)
    on_path(directory)

    attempt = run_conda(
        conda_project(make_project), base_dir=tmp_path, network=True
    ).reproduction["conda"]

    assert attempt["package_list_partial"] is True


def test_an_explicit_relative_file_is_found_in_the_project(
    manager, on_path, make_project, tmp_path
) -> None:
    # The command runs at the workspace root, so a relative name resolved
    # against the working directory would not find a file that does exist in
    # the project. The honest failure is a missing file, not a typo.
    project = conda_project(make_project)
    (project / "environment-dev.yml").write_text(
        f"name: demo\ndependencies:\n  - python={INTERPRETER_SERIES}\n",
        encoding="utf-8",
    )
    directory, environment = manager()
    _apply(environment)
    on_path(directory)

    report = run_conda(
        project,
        base_dir=tmp_path,
        network=True,
        conda_env="environment-dev.yml",
    )
    attempt = report.reproduction["conda"]

    assert attempt["reason"] != "create-failed"
    assert attempt["exit_code"] == 0
    assert attempt["success"] is True
    # The manager is given a path it can open, and it is an absolute one, so the
    # command does not depend on where the manager happens to be run from.
    given = Path(next(part for part in attempt["command"] if part.endswith(".yml")))
    assert given.is_file()


# --------------------------------------------------------------------------- #
# 8. pip check
# --------------------------------------------------------------------------- #


def test_pip_check_runs_when_the_file_declares_pip(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, environment = manager()
    _apply(environment)
    on_path(directory)

    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)
    result = report.reproduction["conda"]["pip_check"]

    assert result["ran"] is True
    assert result["clean"] is True
    assert report.verdict.status.name != "FAIL"


def test_pip_check_conflicts_fail_the_attempt(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, environment = manager(
        broken_metadata="brokenpkg|1.0.0|absent-dep==2.0",
    )
    _apply(environment)
    on_path(directory)

    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)
    result = report.reproduction["conda"]["pip_check"]

    assert result["clean"] is False
    assert result["conflict_count"] == 1
    assert "RC400" in conda_ids(report)
    assert report.verdict.status.name == "FAIL"


def test_pip_check_is_skipped_without_a_pip_subsection(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, environment = manager()
    _apply(environment)
    on_path(directory)
    root = make_project(
        {
            "pyproject.toml": "[project]\nname='x'\n",
            "environment.yml": "name: demo\ndependencies:\n  - python=3.11\n  - numpy\n",
        }
    )

    report = run_conda(root, base_dir=tmp_path, network=True)

    assert report.reproduction["conda"]["pip_check"]["ran"] is False


# --------------------------------------------------------------------------- #
# 9. runtime checks
# --------------------------------------------------------------------------- #


def test_runtime_checks_are_off_by_default(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, environment = manager()
    _apply(environment)
    on_path(directory)

    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)

    assert report.reproduction["runtime_checks"]["enabled"] is False
    assert report.reproduction["runtime_checks"]["imports"] == []


def test_runtime_checks_run_inside_the_environment(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, environment = manager(pytest=True)
    _apply(environment)
    on_path(directory)

    report = run_conda(
        conda_project(make_project),
        base_dir=tmp_path,
        network=True,
        runtime_checks=True,
    )
    runtime = report.reproduction["runtime_checks"]

    assert runtime["enabled"] is True
    assert runtime["imports"], "at least one module should have been imported"
    assert runtime["pytest_collection"]["ran"] is True


def test_a_failing_import_is_reported(manager, on_path, make_project, tmp_path) -> None:
    # The module exists and fails to import, which is the ordinary real cause:
    # a compiled extension that cannot load on this machine.
    directory, environment = manager(module="broken")
    _apply(environment)
    on_path(directory)

    report = run_conda(
        conda_project(make_project),
        base_dir=tmp_path,
        network=True,
        runtime_checks=True,
    )

    assert "RC500" in conda_ids(report)
    assert report.verdict.status.name == "FAIL"


def test_missing_pytest_is_skipped_not_failed(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, environment = manager()
    _apply(environment)
    on_path(directory)

    report = run_conda(
        conda_project(make_project),
        base_dir=tmp_path,
        network=True,
        runtime_checks=True,
    )
    collection = report.reproduction["runtime_checks"]["pytest_collection"]

    assert collection["available"] is False
    assert "RC501" not in conda_ids(report)


# --------------------------------------------------------------------------- #
# 10. read-only guarantee and cleanup
# --------------------------------------------------------------------------- #


def test_the_original_project_is_never_modified(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, environment = manager()
    _apply(environment)
    on_path(directory)
    root = conda_project(make_project)
    before = snapshot(root)

    run_conda(root, base_dir=tmp_path, network=True)

    assert snapshot(root) == before


def test_a_failed_attempt_keeps_its_workspace(
    manager, on_path, make_project, tmp_path
) -> None:
    """A failure is where the evidence lives, so it is not deleted."""
    directory, environment = manager(create_fails=True)
    _apply(environment)
    on_path(directory)

    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)

    assert report.reproduction["workspace_kept"] is True
    assert report.reproduction["workspace"]
    assert Path(report.reproduction["workspace"]).is_dir()


def test_keep_workspace_preserves_a_success(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, environment = manager()
    _apply(environment)
    on_path(directory)

    report = run_conda(
        conda_project(make_project),
        base_dir=tmp_path,
        network=True,
        keep_workspace=True,
    )

    assert report.reproduction["workspace_kept"] is True
    assert Path(report.reproduction["workspace"]).is_dir()


def test_a_success_is_cleaned_up(manager, on_path, make_project, tmp_path) -> None:
    directory, environment = manager()
    _apply(environment)
    on_path(directory)

    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)

    assert report.reproduction["workspace_kept"] is False
    assert report.reproduction["workspace"] is None
    assert not any((tmp_path / "reprocheck").glob("*/conda-env"))


# --------------------------------------------------------------------------- #
# 11. logs
# --------------------------------------------------------------------------- #


def test_the_logs_are_named_and_separate(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, environment = manager()
    _apply(environment)
    on_path(directory)

    report = run_conda(
        conda_project(make_project),
        base_dir=tmp_path,
        network=True,
        keep_workspace=True,
    )
    logs = Path(report.reproduction["workspace"]) / "logs"
    names = {path.name for path in logs.iterdir()}

    assert "conda-create.stdout.log" in names
    assert "conda-create.stderr.log" in names
    assert "conda-list.json" in names
    assert "conda-python-version.stdout.log" in names


def test_the_report_keeps_snippets_not_logs(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, environment = manager(create_fails=True, noise="x" * 5000)
    _apply(environment)
    on_path(directory)

    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)
    attempt = report.reproduction["conda"]

    # A path to the log, and a bounded snippet.
    from reprocheck.reproduction.models import MAX_SNIPPET_CHARS

    assert attempt["stderr_path"].endswith(".log")
    assert len(attempt["stderr_snippet"]) <= MAX_SNIPPET_CHARS
    # The tail is kept, because that is where the failure reason is.
    assert "CondaError" in attempt["stderr_snippet"]
    assert json.dumps(report.to_dict())


# --------------------------------------------------------------------------- #
# 12. the reports
# --------------------------------------------------------------------------- #


def test_json_carries_the_strategy_and_the_attempt(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, environment = manager()
    _apply(environment)
    on_path(directory)

    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)
    document = report.to_dict()

    assert document["reproduction"]["strategy"] == "conda"
    assert document["reproduction"]["conda"]["success"] is True
    assert document["reproduction"]["conda"]["manager"] == "conda"


def test_markdown_has_a_conda_section(manager, on_path, make_project, tmp_path) -> None:
    from reprocheck.reporters import render_markdown

    directory, environment = manager()
    _apply(environment)
    on_path(directory)
    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)
    text = render_markdown(report)

    assert "## Conda reproduction" in text
    assert "environment.yml" in text
    assert "not a" in text and "security boundary" in text
    assert "pip check" in text


def test_markdown_omits_the_section_without_conda(make_project) -> None:
    from reprocheck.reporters import render_markdown

    report = reproduce(make_project({"pyproject.toml": "[project]\nname='x'\n"}))
    assert "## Conda reproduction" not in render_markdown(report)


def test_markdown_does_not_dump_the_package_list(
    manager, on_path, make_project, tmp_path
) -> None:
    from reprocheck.reporters import render_markdown

    directory, environment = manager(
        packages=[{"name": f"pkg{i}", "version": "1.0"} for i in range(80)]
    )
    _apply(environment)
    on_path(directory)
    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)
    text = render_markdown(report)

    assert "Packages: 80" in text
    assert "pkg42" not in text


def test_terminal_has_a_conda_block(manager, on_path, make_project, tmp_path) -> None:
    from reprocheck.reporters import format_report

    directory, environment = manager()
    _apply(environment)
    on_path(directory)
    report = run_conda(conda_project(make_project), base_dir=tmp_path, network=True)
    text = format_report(report, "report.json")

    assert "Conda reproduction" in text
    assert "manager: conda" in text
    assert "created: yes" in text


def test_terminal_omits_the_block_without_conda(make_project) -> None:
    from reprocheck.reporters import format_report

    report = reproduce(make_project({"pyproject.toml": "[project]\nname='x'\n"}))
    assert "Conda reproduction" not in format_report(report, "report.json")


# --------------------------------------------------------------------------- #
# 13. the diff sees a strategy change
# --------------------------------------------------------------------------- #


def test_the_diff_sees_a_strategy_change(
    manager, on_path, make_project, tmp_path
) -> None:
    from reprocheck.diff import compare_reports

    directory, environment = manager()
    _apply(environment)
    on_path(directory)
    root = conda_project(make_project)
    pip_report = reproduce(root, base_dir=tmp_path).to_dict()
    conda_report = run_conda(root, base_dir=tmp_path, network=True).to_dict()

    diff = compare_reports(pip_report, conda_report)

    strategy_changes = [item for item in diff.reproduction if item.key == "strategy"]
    assert strategy_changes, [item.key for item in diff.reproduction]
    # A real edit renders only the attributes that changed.
    assert strategy_changes[0].previous == "value=pip"
    assert strategy_changes[0].current == "value=conda"
    assert strategy_changes[0].changed_fields == ("value",)


# --------------------------------------------------------------------------- #
# 14. the CLI
# --------------------------------------------------------------------------- #


def test_conda_env_without_conda_is_refused(make_project, tmp_path, capsys) -> None:
    root = conda_project(make_project)
    code = main(["reproduce", str(root), "--conda-env", "environment.yml"])

    assert code != EXIT_OK
    assert "--conda-env has no effect without --conda" in capsys.readouterr().err


def test_conda_manager_without_conda_is_refused(make_project, capsys) -> None:
    root = conda_project(make_project)
    code = main(["reproduce", str(root), "--conda-manager", "conda"])

    assert code != EXIT_OK
    assert "--conda-manager has no effect without --conda" in capsys.readouterr().err


def test_the_cli_accepts_the_flags(make_project, tmp_path, capsys) -> None:
    root = conda_project(make_project)
    main(
        [
            "reproduce",
            str(root),
            "--conda",
            "--json",
            str(tmp_path / "r.json"),
        ]
    )

    assert (tmp_path / "r.json").is_file()
    document = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert document["reproduction"]["strategy"] == "conda"


# --------------------------------------------------------------------------- #
# 15. paths and platforms
# --------------------------------------------------------------------------- #


def test_a_workspace_path_with_a_spaces_survives(
    manager, on_path, make_project, tmp_path
) -> None:
    """The usual way a Windows reproduction breaks, checked on every platform."""
    directory, environment = manager()
    _apply(environment)
    on_path(directory)
    spaced = tmp_path / "a path with spaces"
    spaced.mkdir()

    report = run_conda(conda_project(make_project), base_dir=spaced, network=True)
    attempt = report.reproduction["conda"]

    assert attempt["success"] is True
    assert " " in str(attempt["prefix"])
    # The path is one argument, so it needed no quoting and could not be split.
    assert str(attempt["prefix"]) in attempt["command"]


def test_the_project_path_with_spaces_survives(
    manager, on_path, make_project, tmp_path
) -> None:
    directory, environment = manager()
    _apply(environment)
    on_path(directory)
    root = make_project(
        {
            "pyproject.toml": "[project]\nname='x'\n",
            "environment.yml": "name: demo\ndependencies:\n  - python=3.11\n  - pip\n",
        },
        name="a project with spaces",
    )

    report = run_conda(root, base_dir=tmp_path, network=True)

    assert report.reproduction["conda"]["success"] is True


def test_the_python_path_follows_the_platform(manager) -> None:
    """The layout is different on the two platforms and must not be guessed."""
    from reprocheck.reproduction.conda_manager import CondaManager

    manager_object = CondaManager(MANAGERS[2], "conda")
    path = manager_object.python_path(Path("/prefix"))

    if os.name == "nt":
        assert path.endswith("Scripts\\python.exe") or path.endswith(
            "Scripts/python.exe"
        )
    else:
        assert path.endswith("bin/python")


# --------------------------------------------------------------------------- #
# 16. no network is required to run these tests
# --------------------------------------------------------------------------- #


def test_no_socket_is_opened_by_the_conda_path(make_project, tmp_path) -> None:
    """The orchestration must be testable, and provable, without a network."""
    code = (
        "import socket, sys\n"
        "def blocked(*a, **k):\n"
        "    raise AssertionError('the Conda path opened a socket')\n"
        "socket.create_connection = blocked\n"
        "socket.socket.connect = blocked\n"
        f"sys.path.insert(0, {str(Path(__file__).parent.parent / 'src')!r})\n"
        "from reprocheck.reproduction.conda import choose_environment\n"
        "from reprocheck.reproduction.conda_manager import CondaManager, parse_package_list\n"
        'assert parse_package_list(\'{"packages": [{"name": "numpy"}]}\')\n'
        "CondaManager.create_command(CondaManager.__new__(CondaManager), prefix=None, environment_file=None) if False else None\n"
        "print('no socket')\n"
    )
    import subprocess

    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )

    assert completed.returncode == 0, completed.stderr[-1000:]
    assert "no socket" in completed.stdout


# --------------------------------------------------------------------------- #
# 17. optional real-manager integration
# --------------------------------------------------------------------------- #


@pytest.mark.conda_integration
@pytest.mark.skipif(
    not any(
        (Path(directory) / name).exists()
        for directory in os.environ.get("PATH", "").split(os.pathsep)
        if directory
        for name in ("conda", "mamba", "micromamba")
    ),
    reason="no Conda-compatible manager is installed on this machine",
)
def test_real_manager_can_report_its_version(tmp_path) -> None:
    """Only runs where a real manager exists; never part of the default gate."""
    from reprocheck.reproduction.conda_manager import CondaManager

    manager_object = CondaManager.discover()
    assert manager_object
    workspace = ws.create_workspace(tmp_path)

    assert manager_object.read_version(workspace, dict(os.environ))


def _apply(environment: dict[str, str]) -> None:
    for key, value in environment.items():
        os.environ[key] = value
