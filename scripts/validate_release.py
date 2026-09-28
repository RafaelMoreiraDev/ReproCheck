"""Release validation for ReproCheck.

Builds the distribution, checks its metadata, inspects what went into the
artifacts, installs the wheel and the sdist into throwaway environments outside
the source tree, and runs a real command in each. It never uploads anything and
never holds a token.

Usage::

    python scripts/validate_release.py                # everything
    python scripts/validate_release.py --skip-build   # reuse the existing dist/
    python scripts/validate_release.py --audit-only   # inspect dist/ only

Each stage prints PASS or FAIL; the exit code is the number of failed stages.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
WORK = Path(tempfile.gettempdir()) / "reprocheck-release-validation"

#: What must be in the artifacts for the package to work.
REQUIRED_IN_WHEEL = ("reprocheck/cli.py", "reprocheck/__init__.py")
REQUIRED_IN_SDIST = (
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "src/reprocheck/__init__.py",
)

#: What must never be in an artifact.
FORBIDDEN_PARTS = (
    ".git/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".ruff_cache/",
    "dist/",
    "build/",
    "reprocheck-report.",
    "reprocheck-diff.",
    "reprocheck-suggestions.",
    "reprocheck-fix.",
    "baseline-",
    "before.bin",
    ".reprocheck",
    "node_modules/",
)
FORBIDDEN_NAMES = (".env", "id_rsa", "id_ed25519", ".netrc", "credentials")

#: A project with everything the tools need, so the RC140 fix can be exercised.
FIXTURE_FILES = {
    "pyproject.toml": (
        "[build-system]\n"
        'requires = ["setuptools"]\n'
        'build-backend = "setuptools.build_meta"\n'
        "\n"
        "[project]\n"
        'name = "release-fixture"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.11"\n'
        'dependencies = ["httpx==0.27.0"]\n'
        "\n"
        "[tool.uv]\n"
        "dev-dependencies = []\n"
    ),
    ".python-version": "3.11",
    "README.md": "# release fixture\n\n```bash\npip install .\n```\n",
    ".gitignore": "__pycache__/\n",
    "fixture.py": "VALUE = 1\n",
    "tests/__init__.py": "",
    ".github/workflows/ci.yml": (
        "name: ci\non: [push]\njobs:\n  build:\n    steps:\n"
        "      - uses: actions/checkout@v4\n"
    ),
}


class Stage:
    """One named check with a factual result."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.ok = True
        self.notes: list[str] = []

    def fail(self, message: str) -> None:
        self.ok = False
        self.notes.append(f"FAIL {message}")

    def note(self, message: str) -> None:
        self.notes.append(message)

    def report(self) -> bool:
        print(f"\n=== {self.name}: {'PASS' if self.ok else 'FAIL'}")
        for line in self.notes:
            print(f"    {line}")
        return self.ok


def run(
    args: list[str], cwd: Path | None = None, env: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=str(cwd or ROOT), capture_output=True, text=True, check=False, env=env
    )


def build(stage: Stage) -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    completed = run([sys.executable, "-m", "build"])
    if completed.returncode != 0:
        stage.fail(f"`python -m build` exited {completed.returncode}")
        stage.note(completed.stdout[-800:])
        stage.note(completed.stderr[-800:])
        return
    names = sorted(item.name for item in DIST.iterdir())
    stage.note(f"artifacts: {', '.join(names)}")
    if not any(name.endswith(".whl") for name in names):
        stage.fail("no wheel was produced")
    if not any(name.endswith(".tar.gz") for name in names):
        stage.fail("no sdist was produced")


def metadata(stage: Stage) -> None:
    completed = run(
        [
            sys.executable,
            "-m",
            "twine",
            "check",
            "--strict",
            *[str(p) for p in DIST.iterdir()],
        ]
    )
    output = (completed.stdout + completed.stderr).strip()
    if completed.returncode != 0:
        stage.fail(f"twine check exited {completed.returncode}")
    for line in output.splitlines():
        stage.note(line.strip())


def audit(stage: Stage) -> None:
    for path in sorted(DIST.iterdir()):
        if path.suffix == ".whl":
            names = zipfile.ZipFile(path).namelist()
        elif path.name.endswith(".tar.gz"):
            with tarfile.open(path) as archive:
                names = [
                    "/".join(item.name.split("/")[1:]) for item in archive.getmembers()
                ]
        else:  # pragma: no cover - dist/ only holds those two
            continue
        stage.note(f"{path.name}: {len(names)} entries, {path.stat().st_size} bytes")
        required = REQUIRED_IN_WHEEL if path.suffix == ".whl" else REQUIRED_IN_SDIST
        for item in required:
            if not any(name.endswith(item) for name in names):
                stage.fail(f"{path.name} is missing {item}")
        for name in names:
            lowered = name.lower()
            for part in FORBIDDEN_PARTS:
                if part in lowered:
                    stage.fail(f"{path.name} contains {name}")
                    break
            for forbidden in FORBIDDEN_NAMES:
                if lowered.endswith(forbidden):
                    stage.fail(f"{path.name} contains {name}")
        tests = [name for name in names if "/tests/" in f"/{name}"]
        if path.suffix == ".whl" and tests:
            stage.fail(f"{path.name} ships the test suite: {tests[:3]}")
        stage.note(f"{path.name}: {len(names)} entries inspected")


def _make_fixture(directory: Path) -> Path:
    for relative, content in FIXTURE_FILES.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return directory


def _install_and_run(artifact: Path, stage: Stage, *, smoke: bool) -> None:
    if WORK.exists():
        shutil.rmtree(WORK, onerror=_force_remove)
    WORK.mkdir(parents=True)
    # The smoke run needs a state directory of its own: it is the only place
    # where the tool is allowed to write, and it must be outside the source tree.
    state = WORK / "state"
    env_path = WORK / "env"
    environment = dict(os.environ)
    environment["REPROCHECK_STATE_DIR"] = str(state)
    created = run([sys.executable, "-m", "venv", str(env_path)], env=environment)
    if created.returncode != 0:
        stage.fail("could not create the clean environment")
        stage.note(created.stderr[-600:])
        return

    scripts = "Scripts" if sys.platform == "win32" else "bin"
    executable = "python.exe" if sys.platform == "win32" else "python"
    console_name = "reprocheck.exe" if sys.platform == "win32" else "reprocheck"
    python = env_path / scripts / executable
    console = env_path / scripts / console_name
    installed = run(
        [str(python), "-m", "pip", "install", "--quiet", str(artifact)], env=environment
    )
    if installed.returncode != 0:
        stage.fail(f"pip install failed for {artifact.name}")
        stage.note(installed.stdout[-600:])
        stage.note(installed.stderr[-600:])
        return
    stage.note(f"installed {artifact.name} into a clean venv")

    version = run([str(console), "--version"], cwd=WORK, env=environment)
    if version.returncode != 0 or "reprocheck" not in version.stdout:
        stage.fail("`reprocheck --version` failed")
    else:
        stage.note(f"console script: {version.stdout.strip()}")
    module = run(
        [str(python), "-m", "reprocheck", "--version"], cwd=WORK, env=environment
    )
    if module.returncode != 0 or module.stdout.strip() != version.stdout.strip():
        stage.fail("`python -m reprocheck --version` disagrees with the console script")
    else:
        stage.note("python -m reprocheck: same version")
    helped = run([str(console), "--help"], cwd=WORK, env=environment)
    if helped.returncode != 0 or "reprocheck" not in helped.stdout:
        stage.fail("`reprocheck --help` failed")
    else:
        stage.note(f"help: {len(helped.stdout.splitlines())} lines")

    if not smoke:
        return

    fixture = _make_fixture(WORK / "fixture")
    for arguments, label in (
        (["scan", str(fixture)], "scan"),
        (["suggest", str(fixture)], "suggest"),
    ):
        result = run(
            [str(console), *arguments, "--json", str(WORK / f"{label}.json")],
            cwd=WORK,
            env=environment,
        )
        if result.returncode != 0 or not (WORK / f"{label}.json").is_file():
            stage.fail(f"`reprocheck {label}` failed on the clean fixture")
        else:
            stage.note(f"{label}: ok")

    baseline = WORK / "baseline.json"
    saved = run(
        [
            str(console),
            "baseline",
            "save",
            str(WORK / "scan.json"),
            "--output",
            str(baseline),
        ],
        cwd=WORK,
        env=environment,
    )
    compared = run(
        [str(console), "baseline", "compare", str(baseline), str(WORK / "scan.json")],
        cwd=WORK,
        env=environment,
    )
    if saved.returncode != 0 or compared.returncode != 0:
        stage.fail("baseline save/compare failed")
    else:
        stage.note("baseline save + compare: ok")

    dry = run(
        [str(console), "fix", str(fixture), "--suggestion", "FIX-RC140-001"],
        cwd=WORK,
        env=environment,
    )
    if dry.returncode != 0 or "DRY_RUN" not in dry.stdout:
        stage.fail("the fix dry run failed")
    else:
        stage.note("fix dry run: DRY_RUN, nothing written")
    applied = run(
        [str(console), "fix", str(fixture), "--suggestion", "FIX-RC140-001", "--apply"],
        cwd=WORK,
        env=environment,
    )
    if applied.returncode != 0 or "APPLIED" not in applied.stdout:
        stage.fail("the fix application failed")
    else:
        stage.note("fix --apply: APPLIED")

    records = sorted(state.rglob("applied-*.json"))  # one file per --apply
    if not records:
        stage.fail("no application record was found for the rollback")
        return
    record = json.loads(records[-1].read_text(encoding="utf-8"))
    stage.note(f"record: {record['record_id']} backup: {bool(record['backup_path'])}")
    if (
        not (WORK / "fixture" / ".gitignore")
        .read_text(encoding="utf-8")
        .endswith("venv/\n")
    ):
        stage.fail("the applied file does not end with the appended pattern")
    rolled = run(
        [str(console), "fix", str(fixture), "--rollback", str(record["record_id"])],
        cwd=WORK,
        env=environment,
    )
    if rolled.returncode != 0 or "ROLLED_BACK" not in rolled.stdout:
        stage.fail("the rollback failed")
    else:
        stage.note("fix --rollback: ROLLED_BACK")
    restored = (WORK / "fixture" / ".gitignore").read_text(encoding="utf-8")
    if restored != "__pycache__/\n":
        stage.fail(f"the rollback did not restore the original bytes: {restored!r}")
    else:
        stage.note("rollback restored the exact original content")


def _force_remove(function, path, _error) -> None:  # pragma: no cover - Windows only
    """Make a read-only file removable, as a fresh git checkout can produce."""
    import stat

    path.chmod(stat.S_IWRITE)
    function(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true", help="reuse dist/")
    parser.add_argument("--audit-only", action="store_true", help="only inspect dist/")
    parser.add_argument(
        "--smoke",
        default="both",
        choices=["both", "wheel", "sdist", "none"],
        help="which artifact gets the functional smoke run",
    )
    args = parser.parse_args()

    stages: list[Stage] = []
    if not args.skip_build and not args.audit_only:
        stage = Stage("build")
        build(stage)
        stages.append(stage)

    stage = Stage("metadata (twine check --strict)")
    metadata(stage)
    stages.append(stage)

    stage = Stage("artifact audit")
    audit(stage)
    stages.append(stage)

    if not args.audit_only:
        wheels = sorted(DIST.glob("*.whl"))
        sdists = sorted(DIST.glob("*.tar.gz"))
        if args.smoke in {"both", "wheel"} and wheels:
            stage = Stage(f"clean install and smoke: {wheels[-1].name}")
            _install_and_run(wheels[-1], stage, smoke=True)
            stages.append(stage)
        if args.smoke in {"both", "sdist"} and sdists:
            stage = Stage(f"clean install and smoke: {sdists[-1].name}")
            _install_and_run(sdists[-1], stage, smoke=args.smoke == "both")
            stages.append(stage)

    failures = sum(1 for stage in stages if not stage.report())
    print(f"\n{len(stages) - failures}/{len(stages)} stages passed")
    print(f"artifacts: {DIST}")
    print("nothing was uploaded and no token was used")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
