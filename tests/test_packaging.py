"""Packaging and release metadata tests.

These are the checks that can run on every commit. The expensive part of a
release - building, installing into two clean environments and smoke testing -
lives in ``scripts/validate_release.py`` and in CI, and is deliberately not part
of the normal suite.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

from reprocheck import __version__, package_version

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
DISTRIBUTION = PYPROJECT["project"]["name"]


def _installed() -> bool:
    try:
        metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:  # pragma: no cover - source checkout
        return False
    return True


# --------------------------------------------------------------------------- #
# 1. version metadata is the CLI version
# --------------------------------------------------------------------------- #


def test_version_is_defined_in_exactly_one_place() -> None:
    """pyproject declares the version dynamic; only the package holds it."""
    assert "version" in PYPROJECT["project"]["dynamic"]
    assert "version" not in PYPROJECT["project"]
    assert PYPROJECT["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "reprocheck.__version__"
    }


def test_version_follows_pep_440_and_is_a_beta() -> None:
    from packaging.version import Version

    parsed = Version(__version__)
    assert parsed.is_prerelease
    assert parsed.pre is not None and parsed.pre[0] == "b"
    assert str(parsed) == __version__


def test_version_matches_the_installed_metadata() -> None:
    """Fails as soon as the installed distribution disagrees with the source."""
    if not _installed():  # pragma: no cover - source checkout without an install
        pytest.skip(f"{DISTRIBUTION} is not installed in this environment")
    assert metadata.version(DISTRIBUTION) == __version__


def test_package_version_prefers_the_installed_metadata() -> None:
    assert package_version() == (
        metadata.version(DISTRIBUTION) if _installed() else __version__
    )


def test_cli_reports_the_package_version() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "reprocheck", "--version"],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == f"reprocheck {package_version()}"


# --------------------------------------------------------------------------- #
# 2./3. the console script and the module entry point
# --------------------------------------------------------------------------- #


def test_console_script_is_declared() -> None:
    scripts = PYPROJECT["project"]["scripts"]
    assert scripts == {"reprocheck": "reprocheck.cli:main"}


def test_module_entry_point_resolves_to_the_same_function() -> None:
    from reprocheck.cli import main

    module = sys.modules["reprocheck"]
    assert module.__spec__ is not None
    entry = PYPROJECT["project"]["scripts"]["reprocheck"]
    module_name, _, attribute = entry.partition(":")
    assert module_name == "reprocheck.cli"
    assert getattr(sys.modules[module_name], attribute) is main


# --------------------------------------------------------------------------- #
# 4. runtime dependency surface
# --------------------------------------------------------------------------- #


def test_runtime_dependencies_stay_minimal() -> None:
    assert PYPROJECT["project"]["dependencies"] == ["packaging>=23.0"]


def test_dev_extras_carry_the_tooling() -> None:
    extras = PYPROJECT["project"]["optional-dependencies"]
    assert set(extras) == {"dev"}
    names = {item.split(">=")[0].split("==")[0] for item in extras["dev"]}
    assert names == {"pytest", "ruff", "build", "twine"}


def test_requires_python_matches_the_tooling() -> None:
    assert PYPROJECT["project"]["requires-python"] == ">=3.11"
    assert sys.version_info >= (3, 11)


# --------------------------------------------------------------------------- #
# 5. license, author and URLs
# --------------------------------------------------------------------------- #


def test_license_is_mit_and_the_file_exists() -> None:
    assert PYPROJECT["project"]["license"] == "MIT"
    assert PYPROJECT["project"]["license-files"] == ["LICENSE"]
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License")
    assert "Copyright (c) 2026 Rafael Antonio Brito Moreira" in text


def test_author_is_declared() -> None:
    people = PYPROJECT["project"]["authors"] + PYPROJECT["project"]["maintainers"]
    assert all(item["name"] == "Rafael Antonio Brito Moreira" for item in people)


def test_project_urls_are_declared() -> None:
    urls = PYPROJECT["project"]["urls"]
    assert set(urls) == {"Homepage", "Source", "Issues", "Changelog"}
    assert all(value.startswith("https://") for value in urls.values())


def test_readme_and_changelog_are_present() -> None:
    assert PYPROJECT["project"]["readme"] == "README.md"
    assert (ROOT / "README.md").is_file()
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{__version__}]" in changelog
    assert "Known limitations" in changelog


def test_readme_opens_with_what_reprocheck_is() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    head = readme[: readme.index("## How it is organised")]
    assert "## What is ReproCheck?" in head
    for command in (
        "reprocheck scan .",
        "reprocheck reproduce .",
        "--runtime-checks",
        "reprocheck suggest .",
        "reprocheck fix . --suggestion FIX-RC140-001",
        "reprocheck baseline save",
    ):
        assert command in head
    assert "not a security sandbox" in head
    assert "Not proof that a scientific study is reproducible" in head


# --------------------------------------------------------------------------- #
# 6. package discovery
# --------------------------------------------------------------------------- #


def test_packages_are_discovered_under_src() -> None:
    assert PYPROJECT["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]
    package = ROOT / "src" / "reprocheck" / "__init__.py"
    assert package.is_file()
    root = ROOT / "src" / "reprocheck"
    modules = {path.stem for path in root.iterdir() if path.suffix == ".py"}
    packages = {path.name for path in root.iterdir() if path.is_dir()}
    assert {"__init__", "cli", "scanner"} <= modules
    assert {
        "checks",
        "models",
        "reporters",
        "reproduction",
        "fix",
        "diff",
        "suggest",
    } <= packages


def test_workflow_actions_are_pinned_to_a_commit_sha() -> None:
    """The project applies the rule it reports as RC220."""
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    uses = [line for line in workflow.splitlines() if "uses:" in line]
    assert uses, "the workflow should use at least one action"
    for line in uses:
        reference = line.split("uses:", 1)[1].split("#")[0].strip()
        _, _, sha = reference.partition("@")
        assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), reference
        assert "# v" in line, "the human tag must stay next to the pin"


def test_workflow_matrix_matches_the_declared_support() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert 'python-version: ["3.11", "3.12", "3.13"]' in workflow
    assert PYPROJECT["project"]["requires-python"] == ">=3.11"


# --------------------------------------------------------------------------- #
# 7. the release script itself
# --------------------------------------------------------------------------- #


def test_release_script_exists_and_never_uploads() -> None:
    script = (ROOT / "scripts" / "validate_release.py").read_text(encoding="utf-8")
    lowered = script.lower()
    for forbidden in (
        "twine upload",
        "twine.register",
        "pypi.ace-research",
        "password",
        "api_token",
        "os.environ[",
    ):
        assert forbidden not in lowered, (
            f"the release script must not contain {forbidden}"
        )
    assert "nothing was uploaded" in lowered
    assert "twine" in lowered  # it does run the metadata check
    assert "--audit-only" in script


def test_release_script_compiles() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "py_compile",
            str(ROOT / "scripts" / "validate_release.py"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


# --------------------------------------------------------------------------- #
# 8. artifacts, when they exist
# --------------------------------------------------------------------------- #

DIST = ROOT / "dist"


@pytest.mark.skipif(not DIST.is_dir(), reason="dist/ does not exist")
def test_wheel_and_sdist_are_present_and_named_after_the_version() -> None:
    wheels = sorted(DIST.glob("*.whl"))
    sdists = sorted(DIST.glob("*.tar.gz"))
    assert wheels and sdists
    for path in wheels + sdists:
        assert __version__ in path.name, path.name
        assert DISTRIBUTION in path.name


@pytest.mark.skipif(not DIST.is_dir(), reason="dist/ does not exist")
def test_artifacts_carry_no_development_leftovers() -> None:
    import tarfile
    import zipfile

    forbidden = (".git/", "__pycache__/", "/tests/", ".venv/", "reprocheck-report.")
    for path in sorted(DIST.glob("*.whl")):
        names = zipfile.ZipFile(path).namelist()
        assert any(name.endswith("reprocheck/cli.py") for name in names)
        assert not [name for name in names if "/tests/" in f"/{name}"]
        for name in names:
            assert not any(item in name for item in forbidden), name
    for path in sorted(DIST.glob("*.tar.gz")):
        with tarfile.open(path) as archive:
            names = archive.getnames()
        assert any(name.endswith("src/reprocheck/__init__.py") for name in names)
        assert any(name.endswith("LICENSE") for name in names)
        for name in names:
            assert not any(item in name for item in forbidden), name


@pytest.mark.skipif(not DIST.is_dir(), reason="dist/ does not exist")
def test_wheel_metadata_matches_the_source() -> None:
    import zipfile

    path = sorted(DIST.glob("*.whl"))[-1]
    with zipfile.ZipFile(path) as archive:
        name = next(
            item for item in archive.namelist() if item.endswith(".dist-info/METADATA")
        )
        payload = archive.read(name).decode("utf-8")
    fields = {}
    for line in payload.splitlines():
        if not line.strip():
            break
        if ": " in line:
            key, _, value = line.partition(": ")
            fields.setdefault(key.strip(), value.strip())
    assert fields["Metadata-Version"].startswith("2.")
    assert fields["Name"] == DISTRIBUTION
    assert fields["Version"] == __version__
    assert fields["Requires-Python"] == ">=3.11"
    assert fields["License-Expression"] == "MIT" or "MIT" in payload
    assert "Rafael Antonio Brito Moreira" in payload
    assert json.dumps(True)  # keeps the import used and the file honest
    assert "reprocheck" in fields["Summary"] or "reproducibility" in payload
