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
    """The command is ``reprocheck`` whatever the distribution is called."""
    scripts = PYPROJECT["project"]["scripts"]
    assert scripts == {"reprocheck": "reprocheck.cli:main"}


def test_the_three_names_are_deliberately_different() -> None:
    """Index name, command and importable package are three separate things.

    The distribution had to be renamed to ``reprocheck-cli`` because the PyPI
    refuses ``reprocheck`` for similarity with an unrelated project. The brand
    and the command did not move, and this test is here so a future rename does
    not quietly drag them along.
    """
    from reprocheck import DISTRIBUTION_NAME

    assert DISTRIBUTION_NAME == "reprocheck-cli"
    assert PYPROJECT["project"]["name"] == "reprocheck-cli"
    # The command did not change.
    assert set(PYPROJECT["project"]["scripts"]) == {"reprocheck"}
    # The importable package did not change.
    assert (ROOT / "src" / "reprocheck" / "__init__.py").is_file()
    assert sys.modules["reprocheck"].__name__ == "reprocheck"
    # The brand did not change.
    assert (
        "ReproCheck" in PYPROJECT["project"]["description"]
        or "reproducibility" in (PYPROJECT["project"]["description"])
    )
    assert f"pip install {DISTRIBUTION_NAME}" in (ROOT / "README.md").read_text(
        encoding="utf-8"
    )


def test_installed_metadata_is_read_under_the_index_name() -> None:
    """`--version` must not look for a distribution that was renamed."""
    from reprocheck import DISTRIBUTION_NAME, package_version

    assert DISTRIBUTION_NAME == PYPROJECT["project"]["name"]
    if not _installed():
        pytest.skip(f"{DISTRIBUTION} is not installed in this environment")
    assert package_version() == metadata.version(DISTRIBUTION)


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


REPOSITORY = "https://github.com/RafaelMoreiraDev/ReproCheck"


def test_project_urls_are_declared() -> None:
    urls = PYPROJECT["project"]["urls"]
    assert set(urls) == {"Homepage", "Source", "Issues", "Changelog"}
    assert all(value.startswith("https://") for value in urls.values())


def test_project_urls_point_at_the_one_real_repository() -> None:
    """Every URL must be the repository the project actually lives in.

    The metadata used to name a repository that was never created, and the
    check above was happy with any https URL. A published wheel is hard to
    correct, so the target is pinned here.
    """
    urls = PYPROJECT["project"]["urls"]
    expected = {
        "Homepage": f"{REPOSITORY}",
        "Source": f"{REPOSITORY}",
        "Issues": f"{REPOSITORY}/issues",
        "Changelog": f"{REPOSITORY}/blob/main/CHANGELOG.md",
    }
    assert urls == expected
    for text in (ROOT / "CHANGELOG.md", ROOT / "SECURITY.md", ROOT / "SUPPORT.md"):
        body = text.read_text(encoding="utf-8")
        assert REPOSITORY in body, text.name
        assert "github.com/reprocheck/reprocheck" not in body, text.name


def test_security_and_support_policies_exist_and_say_the_important_things() -> None:
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "## What ReproCheck executes" in security
    assert "The virtual environment is not a sandbox" in security
    assert "reprocheck reproduce" in security
    assert "--runtime-checks" in security
    for word in ("container", "VM"):
        assert word in security
    assert "## Reporting a vulnerability in ReproCheck" in security
    # The document must not become an attack manual.
    assert "exploit code" in security
    assert "security/advisories/new" in security

    support = (ROOT / "SUPPORT.md").read_text(encoding="utf-8")
    assert "## Status" in support
    assert "## Requirements" in support
    assert "3.11 or newer" in support
    assert "## Known limitations" in support
    assert "## What ReproCheck is not" in support
    assert "Not a sandbox" in support
    assert "Not proof that a study is reproducible" in support
    assert f"{REPOSITORY}/issues" in support


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
# 7. the release workflow: trusted publishing, no secret
# --------------------------------------------------------------------------- #

RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
RELEASE = RELEASE_WORKFLOW.read_text(encoding="utf-8")


def test_release_workflow_exists() -> None:
    assert RELEASE_WORKFLOW.is_file()
    assert "name: release" in RELEASE


def test_release_workflow_only_runs_for_tags() -> None:
    """A release is a decision, not a side effect of pushing to main."""
    on = RELEASE.split("permissions:", 1)[0]
    assert "tags:" in on
    assert '- "v*"' in on
    assert "branches:" not in on
    assert "workflow_dispatch" not in on


def test_release_workflow_checks_the_tag_against_the_package_version() -> None:
    assert "GITHUB_REF_NAME#v" in RELEASE
    assert "reprocheck.__version__" in RELEASE
    assert "does not match the package version" in RELEASE
    assert "python -m twine check --strict" in RELEASE
    assert "--audit-only" in RELEASE


def test_release_workflow_looks_for_the_artifact_name_setuptools_writes() -> None:
    """The filename is not the metadata name, and the check has to know it.

    The first version looked for ``dist/reprocheck-<version>-...whl``, which
    never exists: setuptools normalises the hyphen to an underscore, so the file
    is ``reprocheck_cli-<version>-...whl``. The release would have failed at the
    version gate for a reason that had nothing to do with the version.
    """
    assert 'wheel="dist/reprocheck_cli-${package_version}-py3-none-any.whl"' in RELEASE
    assert 'sdist="dist/reprocheck_cli-${package_version}.tar.gz"' in RELEASE
    assert "reprocheck-$package_version" not in RELEASE


@pytest.mark.skipif(not (ROOT / "dist").is_dir(), reason="dist/ does not exist")
def test_the_artifacts_on_disk_are_the_ones_the_workflow_looks_for() -> None:
    """Same check, run against what a real build produced."""
    from reprocheck import DISTRIBUTION_NAME

    dist = ROOT / "dist"
    normalised = DISTRIBUTION_NAME.replace("-", "_")
    for suffix in ("-py3-none-any.whl", ".tar.gz"):
        expected = dist / f"{normalised}-{__version__}{suffix}"
        assert expected.is_file(), f"the release workflow expects {expected.name}"


def test_release_workflow_uses_oidc_and_nothing_else() -> None:
    publish = RELEASE.split("publish:", 1)[1]
    assert "id-token: write" in publish
    assert "environment:" in publish
    assert "name: pypi" in publish
    # Minting an OIDC token must not come with repository write access.
    assert "contents: write" not in RELEASE
    assert "pull-requests: write" not in RELEASE


def test_release_workflow_never_carries_a_pypi_secret() -> None:
    for secret in (
        "TWINE_PASSWORD",
        "TWINE_USERNAME",
        "PYPI_API_TOKEN",
        "password:",
        ".pypirc",
        "secrets.",
        "__token__",
    ):
        assert secret not in RELEASE, secret


def test_publish_action_is_the_official_one_pinned_to_a_sha() -> None:
    assert "pypa/gh-action-pypi-publish@" in RELEASE
    line = next(
        line for line in RELEASE.splitlines() if "gh-action-pypi-publish" in line
    )
    _, _, sha = line.split("uses:", 1)[1].split("#")[0].strip().partition("@")
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)
    assert "# v" in line, "the human tag must stay next to the pin"
    # A mutable branch would be the thing RC220 reports.
    assert "@release/v1" not in RELEASE
    assert "@main" not in RELEASE


def test_every_action_in_the_release_workflow_is_pinned() -> None:
    uses = [line for line in RELEASE.splitlines() if "uses:" in line]
    assert len(uses) >= 4, uses
    for line in uses:
        reference = line.split("uses:", 1)[1].split("#")[0].strip()
        _, _, sha = reference.partition("@")
        assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), reference
        assert "# v" in line


def test_the_two_workflows_pin_the_same_shared_actions() -> None:
    """Two workflows using different versions of one action is RC221.

    The first version of the release workflow pinned newer actions, and the
    project's own scan reported it. Both now share one pin, and this test stops
    them drifting apart again.
    """
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    shared = ("actions/checkout", "actions/setup-python")
    for action in shared:
        in_ci = next(line for line in ci.splitlines() if f"{action}@" in line).split(
            "@"
        )[1][:40]
        in_release = next(
            line for line in RELEASE.splitlines() if f"{action}@" in line
        ).split("@")[1][:40]
        assert in_ci == in_release, f"{action}: ci={in_ci} release={in_release}"


def test_project_metadata_and_package_agree_on_the_version() -> None:
    """The value the release workflow compares the tag against."""
    assert PYPROJECT["project"]["dynamic"] == ["version"]
    assert PYPROJECT["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "reprocheck.__version__"
    }
    from reprocheck import __version__ as imported

    assert imported == __version__
    assert __version__ == "0.11.0b3"


def test_changelog_records_the_trusted_publishing_release() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{__version__}]" in changelog
    assert "Trusted Publishing" in changelog
    assert "## [0.11.0b1]" in changelog, "the first beta stays in the history"


def test_changelog_records_the_rename_and_keeps_the_brand() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [0.11.0b3]" in changelog
    assert "reprocheck-cli" in changelog
    # The rename must not read as a rename of the product.
    assert "ReproCheck" in changelog
    assert "## [0.11.0b2]" in changelog, "the prepared-but-unpublished version stays"


def test_self_scan_finds_no_mutable_reference_in_the_release_workflow() -> None:
    """The new workflow must not trip the rule the tool reports as RC220."""
    from reprocheck.scanner import scan

    report = scan(ROOT)
    hits = [
        finding
        for finding in report.findings
        if finding.id == "RC220"
        and RELEASE_WORKFLOW.name in str(finding.evidence or "")
    ]
    assert hits == [], [f"{item.evidence} {item.message}" for item in hits]


# --------------------------------------------------------------------------- #
# 8. the release script itself
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
    # setuptools normalises a hyphen to an underscore in the artifact filename
    # (PEP 427), so the file is reprocheck_cli-... while the metadata Name is
    # reprocheck-cli. Both spellings have to be accepted here, or the rename
    # looks like a broken build.
    normalised = DISTRIBUTION.replace("-", "_")
    for path in wheels + sdists:
        assert __version__ in path.name, path.name
        assert DISTRIBUTION in path.name or normalised in path.name, path.name
    assert wheels[0].name == f"{normalised}-{__version__}-py3-none-any.whl"
    assert sdists[0].name == f"{normalised}-{__version__}.tar.gz"


@pytest.mark.skipif(not DIST.is_dir(), reason="dist/ does not exist")
def test_artifacts_carry_no_development_leftovers() -> None:
    import tarfile
    import zipfile

    # Never in either artifact: build state, caches, local reports.
    forbidden = (".git/", "__pycache__/", ".venv/", "reprocheck-report.", "*.pyc")
    # Never in the wheel: the wheel is what `pip install reprocheck` unpacks,
    # and the test suite is not part of the runtime package.
    wheel_only_forbidden = ("/tests/", "tests/test_")
    for path in sorted(DIST.glob("*.whl")):
        names = zipfile.ZipFile(path).namelist()
        assert any(name.endswith("reprocheck/cli.py") for name in names)
        assert not [name for name in names if "/tests/" in f"/{name}"]
        for name in names:
            assert not any(item in name for item in forbidden), name
            assert not any(item in name for item in wheel_only_forbidden), name
    for path in sorted(DIST.glob("*.tar.gz")):
        with tarfile.open(path) as archive:
            names = archive.getnames()
        assert any(name.endswith("src/reprocheck/__init__.py") for name in names)
        assert any(name.endswith("LICENSE") for name in names)
        for name in names:
            assert not any(item in name for item in forbidden), name
            assert "tests/fixtures" not in name, name
        # The sdist deliberately carries the tests: it is a source
        # distribution, and a user who has to debug a rule needs them.
        assert any("/tests/" in f"/{name}" for name in names), path.name
        assert any(name.endswith("CHANGELOG.md") for name in names), path.name


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
