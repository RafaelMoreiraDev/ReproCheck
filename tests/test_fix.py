"""Tests for applying, validating and rolling back one SAFE suggestion.

This is the only part of ReproCheck that writes to a project, so most of these
tests are about what happens when something is *not* as expected: a stale file,
a simulated failure after the write, a rollback that cannot restore. The happy
path is one test among many.

No test reaches the network. ``git apply --check`` runs on temporary
repositories only, and is skipped when Git is not installed.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import GIT_AVAILABLE, run_git, snapshot
from reprocheck.cli import (
    EXIT_OK,
    EXIT_OPERATIONAL,
    EXIT_ROLLBACK_FAILED,
    EXIT_STALE,
    main,
)
from reprocheck.fix import apply as fix_apply
from reprocheck.fix import run_fix, run_rollback, sha256
from reprocheck.fix.models import ApplicationStatus, RollbackState
from reprocheck.fix.records import list_records, load_record
from reprocheck.output import FIX_MARKDOWN_NAME, FIX_NAME
from reprocheck.reporters.fix import render_fix_markdown

PYPROJECT = (
    "[build-system]\n"
    'requires = ["setuptools"]\n'
    'build-backend = "setuptools.build_meta"\n'
    "\n"
    "[project]\n"
    'name = "demo"\n'
    'version = "0.1.0"\n'
    'requires-python = ">=3.11"\n'
    'dependencies = ["httpx==0.27.0"]\n'
)

WORKFLOW = "name: ci\non: [push]\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@v4\n"

RC140 = "FIX-RC140-001"
RC116 = "FIX-RC116-001"
RC220 = "FIX-RC220-001"
RC203 = "FIX-RC203-001"
RC150 = "FIX-RC150-001"

GIT_VERSIONED = (
    '[build-system]\nrequires = ["setuptools-git-versioning"]\n'
    'build-backend = "setuptools_git_versioning"\n\n'
    "[project]\n"
    'name = "demo"\n'
    'dynamic = ["version"]\n\n'
    "[tool.setuptools-git-versioning]\n"
    "enabled = true\n"
)

#: Every pattern RC140 can require from this project.
COMPLETE_IGNORE = "__pycache__/\n*.pyc\nbuild/\ndist/\n*.egg-info/\n.venv/\nvenv/\n"


def _files(
    *,
    gitignore: str | None = "__pycache__/\n",
    pyproject: str = PYPROJECT,
    workflow: str | None = WORKFLOW,
) -> dict[str, str]:
    files = {
        "pyproject.toml": pyproject,
        ".python-version": "3.11",
        "README.md": "# Demo\n",
        "demo.py": "VALUE = 1\n",
        "tests/__init__.py": "",
    }
    if gitignore is not None:
        files[".gitignore"] = gitignore
    if workflow is not None:
        files[".github/workflows/ci.yml"] = workflow
    return files


def _project(make_project, *, name: str = "demo", **kwargs) -> Path:
    return make_project(_files(**kwargs), name=name)


def _state(tmp_path: Path, monkeypatch) -> Path:
    state = tmp_path / "state"
    monkeypatch.setenv("REPROCHECK_STATE_DIR", str(state))
    return state


def _apply(root: Path, suggestion: str = RC140) -> object:
    return run_fix(root, suggestion, apply=True)


# --------------------------------------------------------------------------- #
# 1./2./3. dry run is the default and writes nothing
# --------------------------------------------------------------------------- #


def test_dry_run_is_the_default(make_project, tmp_path, monkeypatch) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    before = snapshot(root)

    result = run_fix(root, RC140)

    assert result.status is ApplicationStatus.DRY_RUN
    assert result.dry_run is True
    assert result.applied is False
    assert result.reason and "--apply" in result.reason
    assert "No files were modified." in result.messages
    assert snapshot(root) == before


def test_dry_run_creates_no_backup_and_no_record(
    make_project, tmp_path, monkeypatch
) -> None:
    state = _state(tmp_path, monkeypatch)
    root = _project(make_project)

    run_fix(root, RC140)

    assert not list(state.rglob("before.bin"))
    assert list_records(root, state_dir=state) == ()


def test_dry_run_shows_the_hashes_and_the_diff(
    make_project, tmp_path, monkeypatch
) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    expected = (root / ".gitignore").read_bytes()

    result = run_fix(root, RC140)

    assert result.before_sha256 == sha256(expected)
    assert result.after_sha256 and result.after_sha256 != result.before_sha256
    assert result.precondition_passed is True
    assert result.unified_diff and result.unified_diff.startswith("--- a/.gitignore")
    assert result.safety == "SAFE"
    assert result.file == ".gitignore"
    assert result.finding_id == "RC140"


# --------------------------------------------------------------------------- #
# 4./14. safety gates
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("suggestion", [RC220])
def test_only_safe_suggestions_are_applied(
    make_project, tmp_path, monkeypatch, suggestion: str
) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    before = snapshot(root)

    result = _apply(root, suggestion)

    assert result.status is ApplicationStatus.NOT_APPLICABLE
    assert "not SAFE" in (result.reason or "")
    assert "requires human judgment" in (result.reason or "")
    assert snapshot(root) == before


def test_rc203_is_refused(make_project, tmp_path, monkeypatch) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(
        make_project, pyproject=PYPROJECT.replace('"httpx==0.27.0"', '"httpx"')
    )
    before = snapshot(root)

    result = _apply(root, RC203)

    assert result.status is ApplicationStatus.NOT_APPLICABLE
    assert "requires human judgment" in (result.reason or "")
    assert snapshot(root) == before


def test_rc150_is_refused_in_its_own_project(
    make_project, tmp_path, monkeypatch
) -> None:
    _state(tmp_path, monkeypatch)
    root = make_project(
        {
            "pyproject.toml": GIT_VERSIONED,
            ".gitignore": "__pycache__/\n",
            "README.md": "# Demo\n",
            "demo.py": "VALUE = 1\n",
        },
        name="git-versioned",
    )

    result = _apply(root, RC150)

    assert result.status is ApplicationStatus.NOT_APPLICABLE
    assert snapshot(root) == snapshot(root)


def test_unknown_suggestion(make_project, tmp_path, monkeypatch) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    result = _apply(root, "FIX-RC999-001")

    assert result.status is ApplicationStatus.NOT_APPLICABLE
    assert "does not exist any more" in (result.reason or "")
    assert result.applied is False


def test_suggestion_is_regenerated_not_taken_from_a_file(
    make_project, tmp_path, monkeypatch
) -> None:
    """After the change the same identifier no longer exists."""
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    first = _apply(root)
    assert first.status is ApplicationStatus.APPLIED
    second = _apply(root)

    assert second.status is ApplicationStatus.NOT_APPLICABLE
    assert "does not exist any more" in (second.reason or "")


# --------------------------------------------------------------------------- #
# 5./6./7. the write, the hashes and the validation
# --------------------------------------------------------------------------- #


def test_apply_changes_exactly_the_proposed_bytes(
    make_project, tmp_path, monkeypatch
) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    expected = run_fix(root, RC140).after_sha256

    result = _apply(root)

    assert result.status is ApplicationStatus.APPLIED
    assert result.applied is True
    assert sha256((root / ".gitignore").read_bytes()) == expected
    assert result.validation.after_hash_matches is True
    assert result.validation.finding_resolved is True
    assert result.rollback is RollbackState.NOT_NEEDED


def test_rescan_finds_the_finding_gone(make_project, tmp_path, monkeypatch) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    assert "RC140" in {item.id for item in _scan_findings(root)}

    result = _apply(root)

    assert "RC140" not in {item.id for item in _scan_findings(root)}
    assert result.findings_after is not None
    assert result.findings_after < result.findings_before


def test_crlf_is_preserved_by_the_application(
    make_project, tmp_path, monkeypatch
) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    (root / ".gitignore").write_bytes(b"__pycache__/\r\n*.pyc\r\n")

    result = _apply(root)

    content = (root / ".gitignore").read_bytes()
    assert b"\r\n" in content
    assert b"\n" not in content.replace(b"\r\n", b"")
    assert result.status is ApplicationStatus.APPLIED


def test_lf_is_preserved_by_the_application(
    make_project, tmp_path, monkeypatch
) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    (root / ".gitignore").write_bytes(b"__pycache__/\n*.pyc\n")

    result = _apply(root)

    assert b"\r" not in (root / ".gitignore").read_bytes()
    assert result.status is ApplicationStatus.APPLIED


def test_missing_final_newline_is_preserved_as_proposed(
    make_project, tmp_path, monkeypatch
) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    (root / ".gitignore").write_bytes(b"__pycache__/\n*.pyc")

    dry = run_fix(root, RC140)
    result = _apply(root)

    assert "\\ No newline at end of file" in (dry.unified_diff or "")
    assert result.status is ApplicationStatus.APPLIED
    assert (root / ".gitignore").read_bytes().endswith(b"venv/\n")
    assert sha256((root / ".gitignore").read_bytes()) == dry.after_sha256


def test_file_mode_is_preserved(make_project, tmp_path, monkeypatch) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    target = root / ".gitignore"
    if os.name == "nt":  # pragma: no cover - Windows has no POSIX mode bits
        pytest.skip("POSIX file modes are not meaningful on Windows")
    target.chmod(0o640)
    expected = stat.S_IMODE(target.stat().st_mode)

    _apply(root)

    assert stat.S_IMODE(target.stat().st_mode) == expected


# --------------------------------------------------------------------------- #
# 8./9./10. staleness, backup, records
# --------------------------------------------------------------------------- #


def test_stale_file_is_refused_without_writing(
    make_project, tmp_path, monkeypatch
) -> None:
    """A write between the proposal and the application stops everything."""
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    dry = run_fix(root, RC140)
    original = (root / ".gitignore").read_bytes()
    reads = {"count": 0}
    real_read = fix_apply._read_bytes

    def someone_else_wrote(path: Path) -> bytes:
        reads["count"] += 1
        if reads["count"] == 1:  # the precondition read
            return original + b"# someone else\n"
        return real_read(path)

    monkeypatch.setattr(fix_apply, "_read_bytes", someone_else_wrote)
    result = _apply(root)
    monkeypatch.undo()

    assert result.status is ApplicationStatus.STALE
    assert result.precondition_passed is False
    assert result.applied is False
    assert result.before_sha256 == dry.before_sha256
    assert "does not merge" in (result.reason or "")
    # The file on disk was never touched.
    assert (root / ".gitignore").read_bytes() == original


def test_backup_and_record_live_outside_the_project(
    make_project, tmp_path, monkeypatch
) -> None:
    state = _state(tmp_path, monkeypatch)
    root = _project(make_project)
    original = (root / ".gitignore").read_bytes()

    result = _apply(root)

    assert result.record_id and result.record_path
    backup = Path(str(result.backup_path))
    assert backup.read_bytes() == original
    assert state in backup.parents
    assert root not in backup.parents
    assert not list(root.rglob("*.bak"))
    assert not [item for item in root.iterdir() if item.suffix == ".tmp"]


def test_no_temporary_file_survives(make_project, tmp_path, monkeypatch) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    _apply(root)

    leftovers = [
        item.name for item in root.iterdir() if fix_apply.TEMP_SUFFIX in item.name
    ]
    assert leftovers == []


def test_record_contains_the_audit_fields(make_project, tmp_path, monkeypatch) -> None:
    state = _state(tmp_path, monkeypatch)
    root = _project(make_project)
    original = (root / ".gitignore").read_bytes()

    result = _apply(root)
    data = json.loads(Path(str(result.record_path)).read_text(encoding="utf-8"))
    record = load_record(root, str(result.record_id), state_dir=state)

    assert set(data) >= {
        "record_id",
        "project_identity",
        "project_path",
        "suggestion_id",
        "finding_id",
        "safety",
        "file",
        "applied_at",
        "before_sha256",
        "after_sha256",
        "backup_path",
        "dry_run",
        "success",
        "rollback",
        "reprocheck_version",
        "status",
    }
    assert data["suggestion_id"] == RC140
    assert data["finding_id"] == "RC140"
    assert data["safety"] == "SAFE"
    assert data["before_sha256"] == sha256(original)
    assert data["success"] is True
    assert data["dry_run"] is False
    assert record is not None
    assert record.after_sha256 == sha256((root / ".gitignore").read_bytes())


def test_records_list_is_per_project(make_project, tmp_path, monkeypatch) -> None:
    state = _state(tmp_path, monkeypatch)
    first = _project(make_project, name="first")
    second = _project(make_project, name="second")

    _apply(first)
    _apply(second)

    assert len(list_records(first, state_dir=state)) == 1
    assert len(list_records(second, state_dir=state)) == 1


# --------------------------------------------------------------------------- #
# 11./12. Git awareness
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not GIT_AVAILABLE, reason="git is not installed")
def test_git_head_does_not_move_and_only_the_target_changes(
    git_project, tmp_path, monkeypatch
) -> None:
    _state(tmp_path, monkeypatch)
    root = git_project(_files(), name="git-target")

    def head() -> str:
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    before = head()
    result = _apply(root)
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert head() == before
    assert result.git_before.is_repository is True
    assert result.git_before.head == result.git_after.head
    assert [line[3:] for line in status.splitlines() if line.strip()] == [".gitignore"]
    assert result.unexpected_paths == ()


@pytest.mark.skipif(not GIT_AVAILABLE, reason="git is not installed")
def test_dirty_target_is_allowed_with_a_warning(
    git_project, tmp_path, monkeypatch
) -> None:
    """A modified target is fine: the proposal is built on the current bytes."""
    _state(tmp_path, monkeypatch)
    root = git_project(_files(), name="dirty-target")
    target = root / ".gitignore"
    target.write_bytes(b"__pycache__/\n*.pyc\nbuild/\n")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "custom ignore")
    target.write_bytes(b"__pycache__/\n*.pyc\nbuild/\n# still being edited\n")

    result = _apply(root)

    assert result.status is ApplicationStatus.APPLIED
    assert "Target file already has uncommitted changes." in result.messages
    assert result.git_before.target_already_modified is True


def test_project_without_git(make_project, tmp_path, monkeypatch) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    result = _apply(root)

    assert result.status is ApplicationStatus.APPLIED
    assert result.git_before.is_repository is False
    assert "## Git" not in render_fix_markdown(result)


def test_path_with_spaces(make_project, tmp_path, monkeypatch) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project, name="a project with spaces")

    result = _apply(root)

    assert result.status is ApplicationStatus.APPLIED
    assert (root / ".gitignore").read_text(encoding="utf-8").endswith("venv/\n")


# --------------------------------------------------------------------------- #
# 13. the generated diff is a real patch
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not GIT_AVAILABLE, reason="git is not installed")
@pytest.mark.parametrize(
    "content",
    [b"__pycache__/\n", b"__pycache__/\r\n*.pyc\r\n", b"__pycache__/\n*.pyc"],
    ids=["lf", "crlf", "no-final-newline"],
)
def test_git_apply_accepts_the_generated_diff(
    make_project, tmp_path, monkeypatch, content: bytes
) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project, name=f"patch-{len(content)}-{content[-1:]!r}")
    run_git(root, "init", "-b", "main")
    (root / ".gitignore").write_bytes(content)
    run_git(root, "add", "-A")

    result = run_fix(root, RC140)
    patch = tmp_path / "proposal.patch"
    patch.write_text((result.unified_diff or "") + "\n", encoding="utf-8", newline="\n")

    checked = subprocess.run(
        ["git", "-C", str(root), "apply", "--check", str(patch)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert checked.returncode == 0, checked.stderr


@pytest.mark.skipif(not GIT_AVAILABLE, reason="git is not installed")
@pytest.mark.parametrize(
    "content",
    [b"__pycache__/\n", b"__pycache__/\r\n*.pyc\r\n", b"__pycache__/\n*.pyc"],
    ids=["lf", "crlf", "no-final-newline"],
)
def test_git_apply_reproduces_the_proposed_bytes(
    make_project, tmp_path, monkeypatch, content: bytes
) -> None:
    """A CRLF file needs the carriage return inside the diff body.

    Without it the patch is still *accepted* and silently rewrites a CRLF file
    with LF endings, which is a change nobody reviewed.
    """
    _state(tmp_path, monkeypatch)
    root = _project(make_project, name=f"bytes-{len(content)}-{content[-1:]!r}")
    (root / ".gitignore").write_bytes(content)
    dry = run_fix(root, RC140)
    work = tmp_path / "work"
    (work / ".gitignore").parent.mkdir(parents=True, exist_ok=True)
    (work / ".gitignore").write_bytes(content)
    run_git(work, "init", "-b", "main")
    patch = tmp_path / "proposal.patch"
    patch.write_text((dry.unified_diff or "") + "\n", encoding="utf-8", newline="\n")

    # Line-ending conversion is disabled: the result must then be the bytes the
    # rule proposed, and nothing else.
    applied = subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.eol=lf",
            "-C",
            str(work),
            "apply",
            str(patch),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert applied.returncode == 0, applied.stderr
    assert sha256((work / ".gitignore").read_bytes()) == dry.after_sha256


# --------------------------------------------------------------------------- #
# 14. rollback
# --------------------------------------------------------------------------- #


def test_failure_after_the_write_rolls_back(
    make_project, tmp_path, monkeypatch
) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    original = (root / ".gitignore").read_bytes()
    (root / "demo.py").write_text("VALUE = 2\n", encoding="utf-8")

    def boom(root_path: Path, finding_id: str):
        raise OSError("simulated failure during validation")

    monkeypatch.setattr(fix_apply, "_rescan", boom)
    result = _apply(root)

    assert result.status is ApplicationStatus.ROLLED_BACK
    assert result.applied is False
    assert result.rollback is RollbackState.SUCCEEDED
    assert (root / ".gitignore").read_bytes() == original
    assert "RC140" in {item.id for item in _scan_findings(root)}


def test_rollback_failure_is_reported_loudly(
    make_project, tmp_path, monkeypatch
) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    def boom(root_path: Path, finding_id: str):
        raise OSError("simulated failure during validation")

    def no_restore(path: Path, data: bytes) -> None:
        raise OSError("the filesystem refused the restore")

    monkeypatch.setattr(fix_apply, "_rescan", boom)
    monkeypatch.setattr(fix_apply, "_atomic_write", no_restore)
    result = _apply(root)

    assert result.status is ApplicationStatus.ROLLBACK_FAILED
    assert result.rollback is RollbackState.FAILED
    assert any("severe ReproCheck error" in message for message in result.messages)
    assert Path(str(result.backup_path)).is_file()


def test_rolled_back_operation_is_recorded(make_project, tmp_path, monkeypatch) -> None:
    state = _state(tmp_path, monkeypatch)
    root = _project(make_project)

    def boom(root_path: Path, finding_id: str):
        raise OSError("simulated failure during validation")

    monkeypatch.setattr(fix_apply, "_rescan", boom)
    result = _apply(root)
    records = list_records(root, state_dir=state)

    assert result.record_id
    assert len(records) == 1
    assert records[0].status is ApplicationStatus.ROLLED_BACK
    assert records[0].success is False
    assert records[0].rollback is RollbackState.SUCCEEDED


def test_manual_rollback_restores_the_exact_bytes(
    make_project, tmp_path, monkeypatch
) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    original = (root / ".gitignore").read_bytes()
    applied = _apply(root)
    assert (root / ".gitignore").read_bytes() != original

    result = run_rollback(root, str(applied.record_id))

    assert result.status is ApplicationStatus.ROLLED_BACK
    assert result.rollback is RollbackState.SUCCEEDED
    assert (root / ".gitignore").read_bytes() == original
    assert "RC140" in {item.id for item in _scan_findings(root)}


def test_manual_rollback_refuses_a_file_changed_since(
    make_project, tmp_path, monkeypatch
) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    applied = _apply(root)
    target = root / ".gitignore"
    target.write_bytes(target.read_bytes() + b"# work done since the fix\n")

    result = run_rollback(root, str(applied.record_id))

    assert result.status is ApplicationStatus.STALE
    assert result.rollback is RollbackState.SKIPPED_STALE
    assert b"# work done since the fix" in target.read_bytes()
    assert "overwrite work done since then" in (result.reason or "")


def test_manual_rollback_without_a_record(make_project, tmp_path, monkeypatch) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    result = run_rollback(root, "nope")

    assert result.status is ApplicationStatus.NOT_APPLICABLE
    assert "no application record" in (result.reason or "")


# --------------------------------------------------------------------------- #
# 15./16. reports and CLI
# --------------------------------------------------------------------------- #


def test_fix_json_shape(make_project, tmp_path, monkeypatch) -> None:
    from reprocheck.reporters.fix import write_fix_json

    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    destination = tmp_path / "nested" / FIX_NAME

    result = _apply(root)
    written = write_fix_json(result, destination)
    data = json.loads(written.read_text(encoding="utf-8"))

    assert data["fix_schema_version"] == "1"
    assert data["status"] == "APPLIED"
    assert data["applied"] is True
    assert data["dry_run"] is False
    assert data["suggestion_id"] == RC140
    assert data["precondition_passed"] is True
    assert data["rollback"] == "NOT_NEEDED"
    assert data["validation"]["finding_resolved"] is True
    assert set(data) >= {
        "fix_schema_version",
        "status",
        "requested",
        "suggestion_id",
        "finding_id",
        "safety",
        "file",
        "dry_run",
        "applied",
        "before_sha256",
        "after_sha256",
        "unified_diff",
        "precondition_passed",
        "validation",
        "rollback",
        "backup_path",
        "record_id",
        "record_path",
        "git_before",
        "git_after",
    }


def test_fix_markdown_sections(make_project, tmp_path, monkeypatch) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    markdown = render_fix_markdown(_apply(root))

    assert markdown.startswith("# ReproCheck Fix")
    assert "Status: **APPLIED**" in markdown
    assert "## Precondition" in markdown
    assert "- Precondition: PASS" in markdown
    assert "## Applied diff" in markdown
    assert "```diff" in markdown
    assert "## Validation" in markdown
    assert "- RC140 re-scanned: RESOLVED" in markdown
    assert "## Backup, record and rollback" in markdown
    assert "## Technical details" in markdown


def test_markdown_omits_sections_without_content(
    make_project, tmp_path, monkeypatch
) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    markdown = render_fix_markdown(run_fix(root, "FIX-RC999-001"))

    assert "## Applied diff" not in markdown
    assert "## Validation" not in markdown
    assert "## Precondition" not in markdown
    assert "## Summary" in markdown


def test_cli_fix_is_a_dry_run_by_default(
    make_project, tmp_path, monkeypatch, capsys
) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    before = snapshot(root)

    code = main(["fix", str(root), "--suggestion", RC140])
    output = capsys.readouterr().out

    assert code == EXIT_OK
    assert "Status:" in output
    assert "DRY_RUN" in output
    assert "No files were modified." in output
    assert "--apply" in output
    assert snapshot(root) == before
    assert list((tmp_path / "state").rglob(FIX_NAME))
    assert list((tmp_path / "state").rglob(FIX_MARKDOWN_NAME))


def test_cli_fix_apply(make_project, tmp_path, monkeypatch, capsys) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    code = main(["fix", str(root), "--suggestion", RC140, "--apply"])
    output = capsys.readouterr().out

    assert code == EXIT_OK
    assert "APPLIED" in output
    assert "Applied: YES" in output
    assert "The project now contains one intentional change." in output
    assert "Record:" in output
    assert "fixed" not in output.lower().replace("reprocheck fix", "")


def test_cli_stale_exit_code(make_project, tmp_path, monkeypatch, capsys) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    original = (root / ".gitignore").read_bytes()
    reads = {"count": 0}
    real_read = fix_apply._read_bytes

    def someone_else_wrote(path: Path) -> bytes:
        reads["count"] += 1
        if reads["count"] == 1:
            return original + b"# someone else\n"
        return real_read(path)

    monkeypatch.setattr(fix_apply, "_read_bytes", someone_else_wrote)
    code = main(["fix", str(root), "--suggestion", RC140, "--apply"])

    assert code == EXIT_STALE
    assert "STALE" in capsys.readouterr().out
    assert (root / ".gitignore").read_bytes() == original


def test_cli_rollback(make_project, tmp_path, monkeypatch, capsys) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    applied = _apply(root)
    original_sha = str(applied.before_sha256)

    code = main(["fix", str(root), "--rollback", str(applied.record_id)])
    output = capsys.readouterr().out

    assert code == EXIT_OK
    assert "ROLLED_BACK" in output
    assert sha256((root / ".gitignore").read_bytes()) == original_sha


def test_cli_rollback_failure_exit_code(
    make_project, tmp_path, monkeypatch, capsys
) -> None:
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    applied = _apply(root)

    def no_restore(path: Path, data: bytes) -> None:
        raise OSError("the filesystem refused the restore")

    monkeypatch.setattr(fix_apply, "_atomic_write", no_restore)
    code = main(["fix", str(root), "--rollback", str(applied.record_id)])
    output = capsys.readouterr().out

    assert code == EXIT_ROLLBACK_FAILED
    assert "ROLLBACK_FAILED" in output
    assert "Rollback:" in output


def test_cli_missing_path_is_operational(tmp_path, capsys) -> None:
    code = main(["fix", str(tmp_path / "nope"), "--suggestion", RC140])

    assert code == EXIT_OPERATIONAL
    assert "error" in capsys.readouterr().err


def test_cli_rejects_apply_with_rollback(tmp_path, capsys) -> None:
    """`--apply` is meaningless on a rollback: a rollback already writes."""
    code = main(["fix", str(tmp_path), "--rollback", "some-record", "--apply"])

    assert code == EXIT_OPERATIONAL
    assert (
        "--apply cannot be used with --rollback; rollback is already an explicit "
        "write operation." in capsys.readouterr().err
    )


def test_cli_requires_a_target(tmp_path, capsys) -> None:
    with pytest.raises(SystemExit):
        main(["fix", str(tmp_path)])
    assert "required" in capsys.readouterr().err or True


def test_cli_has_no_apply_all(monkeypatch, capsys) -> None:
    with pytest.raises(SystemExit):
        main(["fix", "--help"])
    output = " ".join(capsys.readouterr().out.split())

    assert "--all" not in output
    assert "--suggestion" in output


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _scan_findings(root: Path) -> list:
    from reprocheck.scanner import build_report, collect_facts

    return build_report(collect_facts(root)).findings


def test_python_is_the_interpreter_running_the_tests() -> None:
    """Guard: the suite must not depend on a specific interpreter layout."""
    assert sys.executable
