"""Tests for the suggestion engine, its safety model and the ``suggest`` command.

Every test asserts the same thing in the end: the analysed project is byte for
byte the same before and after. Patches are built in memory and never applied.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from conftest import snapshot
from reprocheck.cli import EXIT_OK, EXIT_OPERATIONAL, main
from reprocheck.output import (
    SUGGESTIONS_MARKDOWN_NAME,
    SUGGESTIONS_NAME,
)
from reprocheck.reporters.suggestions import render_suggestions_markdown
from reprocheck.scanner import build_report, collect_facts
from reprocheck.suggest import Safety, SuggestionKind, suggest, supported_findings
from reprocheck.suggest.models import SUGGESTION_SCHEMA_VERSION
from reprocheck.suggest.patches import NO_NEWLINE_MARKER

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

PYPROJECT = (
    "[build-system]\n"
    'requires = ["setuptools"]\n'
    'build-backend = "setuptools.build_meta"\n'
    "\n"
    "[project]\n"
    'name = "demo"\n'
    'version = "0.1.0"\n'
    'requires-python = ">=3.11"\n'
    'dependencies = ["numpy", "httpx==0.27.0"]\n'
    "\n"
    "[tool.uv]\n"
    "dev-dependencies = []\n"
)

WORKFLOW = (
    "name: ci\n"
    "on: [push]\n"
    "jobs:\n"
    "  build:\n"
    "    steps:\n"
    "      - uses: actions/checkout@v4\n"
)

README = (
    "# Demo\n\n"
    "Install:\n\n"
    "```bash\n"
    "pip install -r requirements.txt\n"
    "python run.py\n"
    "```\n"
)


def _project(
    make_project,
    *,
    gitignore: str | None = "__pycache__/\n*.pyc\n",
    readme: str = README,
    workflow: str | None = WORKFLOW,
    name: str = "demo",
) -> Path:
    files = {
        "pyproject.toml": PYPROJECT,
        ".python-version": "3.11",
        "README.md": readme,
        "demo.py": "VALUE = 1\n",
        "tests/__init__.py": "",
    }
    if gitignore is not None:
        files[".gitignore"] = gitignore
    if workflow is not None:
        files[".github/workflows/ci.yml"] = workflow
    return make_project(files, name=name)


def _write_bytes(root: Path, relative: str, content: bytes) -> None:
    """Write a file with exact bytes, so newline style is under test control."""
    (root / relative).write_bytes(content)


def _suggest(root: Path):
    return suggest(collect_facts(root), build_report(collect_facts(root)))


def _one(root: Path):
    report = _suggest(root)
    assert len(report.safe) == 1, [item.suggestion_id for item in report.suggestions]
    return report.safe[0]


def _ids(root: Path) -> set[str]:
    return {finding.id for finding in build_report(collect_facts(root)).findings}


# --------------------------------------------------------------------------- #
# 1./2. RC140 produces one SAFE proposal for all patterns
# --------------------------------------------------------------------------- #


def test_rc140_produces_a_safe_suggestion(make_project) -> None:
    root = _project(make_project, gitignore="__pycache__/\n*.pyc\n")
    before = snapshot(root)

    suggestion = _one(root)

    assert "RC140" in _ids(root)
    assert suggestion.finding_id == "RC140"
    assert suggestion.suggestion_id == "FIX-RC140-001"
    assert suggestion.safety is Safety.SAFE
    assert suggestion.kind is SuggestionKind.FIX_AVAILABLE
    assert suggestion.file == ".gitignore"
    assert suggestion.unified_diff is not None
    assert suggestion.can_auto_apply_later is True
    assert suggestion.requires_user_approval is True
    # The precondition is a hash of the exact bytes, not of the parsed text.
    assert (
        suggestion.before_sha256
        == hashlib.sha256((root / ".gitignore").read_bytes()).hexdigest()
    )
    assert (
        suggestion.after_sha256
        == hashlib.sha256(suggestion.after.encode("utf-8")).hexdigest()
    )
    assert snapshot(root) == before


def test_six_missing_patterns_are_one_patch(make_project) -> None:
    root = _project(make_project, gitignore="__pycache__/\n")
    suggestion = _one(root)

    added = _added_lines(suggestion.unified_diff)
    assert added == ["*.egg-info/", ".venv/", "build/", "dist/", "venv/"]
    hunks = [
        line for line in suggestion.unified_diff.splitlines() if line.startswith("@@")
    ]
    assert len(hunks) == 1
    assert [item.finding_id for item in _suggest(root).suggestions].count("RC140") == 1


def _added_lines(diff: str) -> list[str]:
    body = diff.splitlines()[2:]  # drop ---, +++ and the hunk header
    return [
        line[1:] for line in body if line.startswith("+") and not line.startswith("+++")
    ]


#: Every pattern RC140 can require here: Python files, a build backend and a
#: pinned virtual environment are all evidenced by this project.
COMPLETE_IGNORE = "__pycache__/\n*.pyc\nbuild/\ndist/\n*.egg-info/\n.venv/\nvenv/\n"


def test_pattern_already_present_is_not_duplicated(make_project) -> None:
    root = _project(make_project, gitignore=COMPLETE_IGNORE)

    report = _suggest(root)

    assert not [item for item in report.suggestions if item.finding_id == "RC140"]
    assert "RC140" not in _ids(root)


# --------------------------------------------------------------------------- #
# 4./5./6. newline styles, no final newline, no .gitignore
# --------------------------------------------------------------------------- #


def test_crlf_is_preserved(make_project) -> None:
    root = _project(make_project, gitignore="__pycache__/\n*.pyc\n")
    _write_bytes(root, ".gitignore", b"__pycache__/\r\n*.pyc\r\n")

    suggestion = _one(root)

    assert suggestion.before == "__pycache__/\r\n*.pyc\r\n"
    assert suggestion.after.endswith("\r\n")
    assert "\n" not in suggestion.after.replace("\r\n", "")
    assert "\\ No newline" not in suggestion.unified_diff


def test_lf_is_preserved(make_project) -> None:
    root = _project(make_project, gitignore="__pycache__/\n*.pyc\n")
    _write_bytes(root, ".gitignore", b"__pycache__/\n*.pyc\n")

    suggestion = _one(root)

    assert suggestion.after.endswith("\n")
    assert "\r" not in suggestion.after


def test_missing_final_newline_is_handled(make_project) -> None:
    root = _project(make_project, gitignore="__pycache__/\n*.pyc\n")
    _write_bytes(root, ".gitignore", b"__pycache__/\n*.pyc")

    suggestion = _one(root)

    assert suggestion.after.startswith("__pycache__/\n*.pyc\n")
    assert NO_NEWLINE_MARKER in suggestion.unified_diff
    assert "-*.pyc" in suggestion.unified_diff
    assert "+*.pyc" in suggestion.unified_diff
    assert any("no final newline" in text for text in suggestion.limitations)


def test_unified_diff_shape(make_project) -> None:
    root = _project(make_project, gitignore="__pycache__/\n*.pyc\n")

    diff = _one(root).unified_diff
    lines = diff.splitlines()

    assert lines[0] == "--- a/.gitignore"
    assert lines[1] == "+++ b/.gitignore"
    assert lines[2].startswith("@@ -1,")
    assert " __pycache__/" in lines
    assert "+build/" in lines
    assert all(
        line.startswith(("---", "+++", "@@", " ", "+", "-", "\\")) for line in lines
    )


def test_missing_gitignore_gets_no_proposal(make_project) -> None:
    """Creating a .gitignore is a decision about the project: not a safe fix."""
    root = _project(make_project, gitignore=None)
    before = snapshot(root)

    report = _suggest(root)

    assert not [item for item in report.suggestions if item.finding_id == "RC140"]
    assert "RC140" not in _ids(root)
    assert snapshot(root) == before


# --------------------------------------------------------------------------- #
# 8. idempotency
# --------------------------------------------------------------------------- #


def test_suggestion_is_idempotent(make_project) -> None:
    root = _project(make_project, gitignore="__pycache__/\n")
    first = _one(root)

    _write_bytes(root, ".gitignore", first.after.encode("utf-8"))
    second = _suggest(root)

    assert not [item for item in second.suggestions if item.finding_id == "RC140"]
    assert "RC140" not in _ids(root)


def test_proposed_content_is_stable_when_reapplied(make_project) -> None:
    """The proposal text is the file content itself, so re-applying is a no-op."""
    root = _project(make_project, gitignore="__pycache__/\n")
    first = _one(root)

    # Apply the proposal the way a patch would: exact bytes, no translation.
    _write_bytes(root, ".gitignore", first.after.encode("utf-8"))

    assert (root / ".gitignore").read_bytes().decode("utf-8") == first.after
    assert _suggest(root).summary.fix_available == 0


# --------------------------------------------------------------------------- #
# 9. No invented values
# --------------------------------------------------------------------------- #


def test_rc203_never_invents_a_version(make_project) -> None:
    root = _project(make_project)
    suggestion = next(
        item for item in _suggest(root).suggestions if item.finding_id == "RC203"
    )

    assert suggestion.safety is Safety.MANUAL_ONLY
    assert suggestion.unified_diff is None
    assert suggestion.before is None and suggestion.after is None
    assert "no version is chosen" in suggestion.limitations
    assert not any(
        character.isdigit() for text in suggestion.limitations for character in text
    )


def test_rc220_never_invents_a_sha(make_project) -> None:
    root = _project(make_project)
    suggestions = [
        item for item in _suggest(root).suggestions if item.finding_id == "RC220"
    ]

    assert suggestions, "the workflow references actions/checkout@v4"
    for suggestion in suggestions:
        assert suggestion.safety is Safety.REVIEW_REQUIRED
        assert suggestion.kind is SuggestionKind.REVIEW_REQUIRED
        assert suggestion.unified_diff is None
        assert "40" in suggestion.rationale or "SHA" in suggestion.title
        assert "no network" in suggestion.rationale


def test_rc150_has_no_patch(make_project) -> None:
    files = {
        "pyproject.toml": (
            "[build-system]\n"
            'requires = ["setuptools-git-versioning"]\n'
            'build-backend = "setuptools_git_versioning"\n'
            "\n"
            "[project]\n"
            'name = "demo"\n'
            'dynamic = ["version"]\n'
            "\n"
            "[tool.setuptools-git-versioning]\n"
            "enabled = true\n"
        ),
        ".gitignore": "__pycache__/\n",
        "README.md": "# Demo\n",
        "demo.py": "VALUE = 1\n",
    }
    root = make_project(files, name="git-versioned")
    suggestion = next(
        item for item in _suggest(root).suggestions if item.finding_id == "RC150"
    )

    assert suggestion.safety is Safety.MANUAL_ONLY
    assert suggestion.unified_diff is None
    assert suggestion.can_auto_apply_later is False
    assert "fallback version" in suggestion.description


def test_rc121_never_invents_a_path(make_project) -> None:
    files = {
        "pyproject.toml": PYPROJECT,
        "README.md": "# Demo\n",
        ".gitignore": "__pycache__/\n",
        "demo.py": 'import csv\ncsv.read_csv("absent.csv")\n',
    }
    root = make_project(files, name="missing-path")
    suggestion = next(
        item for item in _suggest(root).suggestions if item.finding_id == "RC121"
    )

    assert suggestion.safety is Safety.MANUAL_ONLY
    assert suggestion.unified_diff is None
    assert "no path is invented" in suggestion.limitations
    assert not (root / "absent.csv").exists()


def test_readme_missing_script_is_not_created(make_project) -> None:
    root = _project(make_project)
    before = snapshot(root)

    report = _suggest(root)
    scripts = [item for item in report.suggestions if item.finding_id == "RC131"]

    assert scripts and scripts[0].safety is Safety.MANUAL_ONLY
    assert scripts[0].unified_diff is None
    assert not (root / "run.py").exists()
    assert (root / "README.md").read_text(encoding="utf-8") == README
    assert snapshot(root) == before


def test_readme_missing_requirements_is_not_created(make_project) -> None:
    root = _project(make_project)
    report = _suggest(root)
    requirements = [item for item in report.suggestions if item.finding_id == "RC130"]

    assert requirements and requirements[0].safety is Safety.MANUAL_ONLY
    assert not (root / "requirements.txt").exists()


def test_readme_missing_directory_is_not_created(make_project) -> None:
    readme = "# Demo\n\nRun it:\n\n```bash\ncd scripts\n```\n"
    root = _project(make_project, readme=readme)
    report = _suggest(root)

    assert "RC132" in {item.finding_id for item in report.suggestions}
    assert not (root / "scripts").exists()


def test_rc116_does_not_generate_a_lockfile(make_project) -> None:
    root = _project(make_project)
    suggestion = next(
        item for item in _suggest(root).suggestions if item.finding_id == "RC116"
    )

    assert suggestion.safety is Safety.MANUAL_ONLY
    assert suggestion.unified_diff is None
    assert not (root / "uv.lock").exists()


# --------------------------------------------------------------------------- #
# 14./15. read-only guarantee
# --------------------------------------------------------------------------- #


def test_suggest_does_not_touch_the_project(make_project) -> None:
    root = _project(make_project)
    before = snapshot(root)

    _suggest(root)

    assert snapshot(root) == before


def test_git_state_is_unchanged(git_project) -> None:
    root = git_project(_gitignore_only(), name="git-state")

    def state() -> tuple[str, str]:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return head, status

    before = state()
    _suggest(root)

    assert state() == before
    assert before[1] == ""


def _gitignore_only() -> dict[str, str]:
    return {
        "pyproject.toml": PYPROJECT,
        ".python-version": "3.11",
        "README.md": "# Demo\n",
        ".gitignore": "__pycache__/\n",
        "demo.py": "VALUE = 1\n",
    }


# --------------------------------------------------------------------------- #
# 16. determinism
# --------------------------------------------------------------------------- #


def test_suggestions_are_deterministic(make_project) -> None:
    root = _project(make_project)

    first = _suggest(root).to_dict()
    second = _suggest(root).to_dict()

    first["scan_timestamp"] = second["scan_timestamp"] = ""
    assert json.dumps(first) == json.dumps(second)
    assert [item["suggestion_id"] for item in first["suggestions"]] == sorted(
        item["suggestion_id"] for item in first["suggestions"]
    )


# --------------------------------------------------------------------------- #
# 17. reports
# --------------------------------------------------------------------------- #


def test_suggestions_json_shape(make_project, tmp_path) -> None:
    from reprocheck.reporters.suggestions import write_suggestions_json

    root = _project(make_project)
    destination = tmp_path / "nested" / SUGGESTIONS_NAME

    written = write_suggestions_json(_suggest(root), destination)
    data = json.loads(written.read_text(encoding="utf-8"))

    assert data["suggestion_schema_version"] == SUGGESTION_SCHEMA_VERSION
    assert data["project"]["name"] == "demo"
    assert set(data) == {
        "suggestion_schema_version",
        "reprocheck_version",
        "scan_timestamp",
        "project",
        "summary",
        "suggestions",
        "no_proposal",
    }
    summary = data["summary"]
    assert summary["findings"] >= summary["fix_available"]
    assert summary["fix_available"] == 1
    assert summary["patches"] == 1
    for item in data["suggestions"]:
        assert set(item) == {
            "suggestion_id",
            "finding_id",
            "kind",
            "title",
            "safety",
            "confidence",
            "file",
            "description",
            "rationale",
            "before",
            "after",
            "before_sha256",
            "after_sha256",
            "unified_diff",
            "requires_user_approval",
            "can_auto_apply_later",
            "limitations",
        }
        assert item["requires_user_approval"] is True
        assert (
            item["kind"]
            == {
                "SAFE": "FIX_AVAILABLE",
                "REVIEW_REQUIRED": "REVIEW_REQUIRED",
                "MANUAL_ONLY": "NO_AUTOFIX",
            }[item["safety"]]
        )


def test_every_finding_is_traceable(make_project) -> None:
    """Every problem is either proposed for or explicitly declined."""
    root = _project(make_project)
    report = _suggest(root)
    proposed = {item.finding_id for item in report.suggestions}
    declined = {item.finding_id for item in report.skipped}
    findings = build_report(collect_facts(root)).findings
    problems = {item.id for item in findings if item.severity.value != "info"}

    assert problems <= proposed | declined
    assert report.summary.no_proposal == len(declined)
    assert all(item.reason for item in report.skipped), (
        "a declined finding always says why"
    )


def test_markdown_sections(make_project) -> None:
    root = _project(make_project)
    markdown = render_suggestions_markdown(_suggest(root))

    assert markdown.startswith("# ReproCheck Fix Suggestions")
    assert "**Nothing here has been applied.**" in markdown
    assert "## Summary" in markdown
    assert "## Safe fixes" in markdown
    assert "### FIX-RC140-001" in markdown
    assert "```diff" in markdown
    assert "## Review required" in markdown
    assert "## Manual investigation" in markdown
    assert "No patch generated." in markdown
    assert "## No proposal" in markdown
    assert "## Technical details" in markdown
    assert "no model, no inference and no network access" in markdown


def test_markdown_omits_empty_sections(make_project) -> None:
    files = {
        "pyproject.toml": PYPROJECT.replace('"numpy", ', '"numpy==1.26.0", ').replace(
            "\n[tool.uv]\ndev-dependencies = []\n", ""
        ),
        ".python-version": "3.11",
        "README.md": "# Demo\n",
        ".gitignore": COMPLETE_IGNORE + ".pytest_cache/\n.ruff_cache/\n",
        "demo.py": "VALUE = 1\n",
        "tests/__init__.py": "",
    }
    root = make_project(files, name="clean")
    report = _suggest(root)
    markdown = render_suggestions_markdown(report)

    assert not report.suggestions
    assert "## Safe fixes" not in markdown
    assert "## Review required" not in markdown
    assert "## Manual investigation" not in markdown
    assert "## No proposal" in markdown  # RC004: not a Git repository


def test_supported_findings_are_documented() -> None:
    assert set(supported_findings()) == {
        "RC116",
        "RC121",
        "RC130",
        "RC131",
        "RC132",
        "RC140",
        "RC150",
        "RC203",
        "RC220",
    }


# --------------------------------------------------------------------------- #
# 18. CLI
# --------------------------------------------------------------------------- #


def test_cli_suggest(make_project, tmp_path, capsys) -> None:
    root = _project(make_project)
    before = snapshot(root)

    code = main(
        [
            "suggest",
            str(root),
            "--json",
            str(tmp_path / "s.json"),
            "--markdown",
            str(tmp_path / "s.md"),
        ]
    )
    output = capsys.readouterr().out

    assert code == EXIT_OK
    assert "ReproCheck suggestions" in output
    assert "Safe fixes:       1" in output
    assert "No files were modified." in output
    assert "FIX-RC140-001" in output
    assert (tmp_path / "s.json").is_file()
    assert (tmp_path / "s.md").is_file()
    assert snapshot(root) == before


def test_cli_suggest_default_destination(make_project, tmp_path, monkeypatch) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("REPROCHECK_STATE_DIR", str(state))
    root = _project(make_project)

    assert main(["suggest", str(root)]) == EXIT_OK

    assert list(state.rglob(SUGGESTIONS_NAME))
    assert list(state.rglob(SUGGESTIONS_MARKDOWN_NAME))


def test_cli_suggest_missing_path(tmp_path, capsys) -> None:
    code = main(["suggest", str(tmp_path / "nope")])

    assert code == EXIT_OPERATIONAL
    assert "does not exist" in capsys.readouterr().err


def test_suggest_has_no_apply_option(capsys) -> None:
    """V0.9 proposes only: ``suggest`` never writes, ``fix`` is a separate command."""
    with pytest.raises(SystemExit):
        main(["suggest", "--help"])
    output = capsys.readouterr().out

    assert "--apply" not in output
    assert "--force" not in output


def test_project_without_fixable_findings(make_project, tmp_path) -> None:
    files = {
        "pyproject.toml": PYPROJECT.replace('"numpy", ', '"numpy==1.26.0", ').replace(
            "\n[tool.uv]\ndev-dependencies = []\n", ""
        ),
        ".python-version": "3.11",
        "README.md": "# Demo\n\nNo commands here.\n",
        ".gitignore": COMPLETE_IGNORE + ".pytest_cache/\n.ruff_cache/\n",
        "demo.py": "VALUE = 1\n",
        "tests/__init__.py": "",
    }
    root = make_project(files, name="nothing-to-fix")
    before = snapshot(root)

    report = _suggest(root)

    assert not report.safe
    assert report.summary.patches == 0
    assert "## Safe fixes" not in render_suggestions_markdown(report)
    assert snapshot(root) == before
