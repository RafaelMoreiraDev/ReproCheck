"""Post-fix verification: what it proves, and what it is not.

`--verify` answers one question: after the bytes were replaced, does the project
look the way it was supposed to? It answers it by re-scanning statically, and
these tests are mostly about the ways that can be untrue: a target that did not
go away, a new error that appeared, a file that somebody else wrote in between.

The seams used to make those failures deterministic are deliberately few. The
write, the atomic replace, the backup, the rescan, the identity comparison, the
integrity fingerprint and the rollback all run for real; a test hooks the rescan
so it can introduce one specific fact and then gets out of the way. A suite that
patched the verifier itself would pass while the product was broken.

Nothing here reaches the network and nothing executes project code, which is
also what `--verify` promises.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import GIT_AVAILABLE, run_git, snapshot
from reprocheck.cli import (
    EXIT_OK,
    EXIT_OPERATIONAL,
    EXIT_ROLLED_BACK,
    EXIT_STALE,
    main,
)
from reprocheck.fix import apply as fix_apply
from reprocheck.fix import run_fix, sha256
from reprocheck.fix.models import (
    ApplicationStatus,
    FixVerification,
    RollbackState,
)
from reprocheck.fix.verify import (
    diff_paths,
    identities,
    snapshot_project,
    verify_application,
)
from reprocheck.suggest import Safety

RC140 = "FIX-RC140-001"
#: What the suggestion produces, in the order it produces it: the patterns the
#: file already had, then the missing ones sorted. Read with ``read_text`` the
#: line endings come back as ``\n``; the bytes on disk are CRLF, which is what
#: the other constant is for.
COMPLETE_IGNORE = "__pycache__/\n*.egg-info/\n.venv/\nbuild/\ndist/\nvenv/\n"
COMPLETE_IGNORE_CRLF = COMPLETE_IGNORE.replace("\n", "\r\n").encode("utf-8")
PYPROJECT = (
    "[build-system]\n"
    'requires = ["setuptools"]\n'
    'build-backend = "setuptools.build_meta"\n'
    "\n"
    "[project]\n"
    'name = "demo"\n'
    'version = "0.1.0"\n'
    'requires-python = ">=3.11"\n'
    "dependencies = []\n"
)


def _files(*, gitignore: str | None = "__pycache__/\n", extra: dict | None = None):
    files = {
        "pyproject.toml": PYPROJECT,
        ".python-version": "3.11",
        "README.md": "# Demo\n",
        "demo.py": "VALUE = 1\n",
    }
    if gitignore is not None:
        files[".gitignore"] = gitignore
    for name, content in (extra or {}).items():
        files[name] = content
    return files


def _project(make_project, *, name: str = "demo", **kwargs) -> Path:
    return make_project(_files(**kwargs), name=name)


def _state(tmp_path: Path, monkeypatch) -> Path:
    state = tmp_path / "state"
    monkeypatch.setenv("REPROCHECK_STATE_DIR", str(state))
    return state


def _apply_verify(root: Path, suggestion: str = RC140):
    return run_fix(root, suggestion, apply=True, verify=True)


def _proposed_bytes(root: Path) -> bytes:
    """The bytes the RC140 suggestion proposes for the file as it is now.

    Read from the suggestion engine rather than written out here, because the
    engine keeps the line endings of the file it read and a literal in a test
    pins the platform instead of the behaviour.
    """
    from reprocheck.scanner import build_report, collect_facts
    from reprocheck.suggest import suggest

    report = build_report(collect_facts(root))
    chosen = next(
        item
        for item in suggest(collect_facts(root), report).suggestions
        if item.suggestion_id == RC140
    )
    return str(chosen.after).encode("utf-8")


def _check(result, name: str):
    return next(item for item in result.verification.checks if item.name == name)


def _during_rescan(monkeypatch, effect):
    """Run ``effect(root)`` after the write and before the verification.

    Hooked at the rescan because that is the one moment inside the real pipeline
    where "something else happened" can be introduced honestly: the write has
    happened, and the verification has not started.
    """
    original = fix_apply._rescan

    def wrapper(root, finding_id):
        effect(root)
        return original(root, finding_id)

    monkeypatch.setattr(fix_apply, "_rescan", wrapper)


# --------------------------------------------------------------------------- #
# 1./2. the flag itself
# --------------------------------------------------------------------------- #


def test_verify_without_apply_is_refused(make_project, tmp_path, monkeypatch, capsys):
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    code = main(["fix", str(root), "--suggestion", RC140, "--verify"])

    assert code == EXIT_OPERATIONAL
    message = capsys.readouterr().err
    assert "--verify has no meaning without --apply" in message
    # Refused before anything was read for writing, and definitely before a write.
    assert (root / ".gitignore").read_text(encoding="utf-8") == "__pycache__/\n"


def test_apply_without_verify_behaves_exactly_as_before(
    make_project, tmp_path, monkeypatch
):
    """The default must not have moved."""
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    result = run_fix(root, RC140, apply=True)

    assert result.status is ApplicationStatus.APPLIED
    assert result.applied is True
    assert result.verification.attempted is False
    assert result.verification.not_attempted_reason == "not_requested"
    assert (root / ".gitignore").read_text(encoding="utf-8") == COMPLETE_IGNORE


def test_verify_with_rollback_is_refused(make_project, tmp_path, monkeypatch, capsys):
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    record = run_fix(root, RC140, apply=True)

    code = main(["fix", str(root), "--rollback", str(record.record_id), "--verify"])

    assert code == EXIT_OPERATIONAL
    assert "rollback is already a write" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# 3./4./5. the target
# --------------------------------------------------------------------------- #


def test_rc140_applies_and_verifies(make_project, tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    before = snapshot(root)

    result = _apply_verify(root)

    assert result.status is ApplicationStatus.APPLIED
    assert result.verification.attempted is True
    assert result.verification.success is True
    assert result.verification.target_resolved is True
    assert result.verification.target_finding_id == "RC140"
    assert result.verification.target_before == 1
    assert result.verification.target_after == 0
    assert result.verification.regressions == ()
    assert result.verification.removed == ("RC140|.gitignore|.gitignore",)
    assert result.verification.added == ()
    assert (root / ".gitignore").read_text(encoding="utf-8") == COMPLETE_IGNORE
    assert _check(result, "target-resolved").passed is True
    assert _check(result, "content-intact").passed is True
    assert _check(result, "no-regressions").passed is True
    assert _check(result, "project-integrity").passed is True
    assert set(_changed(before, snapshot(root))) == {".gitignore"}


def test_the_target_is_matched_by_identity_and_not_by_a_count(
    make_project, tmp_path, monkeypatch
):
    """A total that fell proves nothing about which finding went."""
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    result = _apply_verify(root)

    removed = result.verification.removed
    assert removed and all(item.startswith("RC140|") for item in removed)
    assert result.verification.findings_after < result.verification.findings_before


def test_a_target_that_stays_fails_and_is_reverted(make_project, tmp_path, monkeypatch):
    # The written bytes deliberately still lack the patterns, so the rule keeps
    # reporting. Everything else in the pipeline is the real one.
    import dataclasses

    from reprocheck.suggest import suggest as real_suggest

    def weaken(facts, report):
        proposals = real_suggest(facts, report)
        for item in proposals.suggestions:
            if item.suggestion_id != RC140:
                continue
            content = (root / ".gitignore").read_text(encoding="utf-8")
            return dataclasses.replace(
                proposals,
                suggestions=[
                    dataclasses.replace(
                        item,
                        after=content,
                        after_sha256=sha256(content.encode("utf-8")),
                    )
                    if other.suggestion_id == RC140
                    else other
                    for other in proposals.suggestions
                ],
            )
        return proposals

    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    before = (root / ".gitignore").read_bytes()
    monkeypatch.setattr(fix_apply, "suggest", weaken)

    result = _apply_verify(root)

    assert result.status is ApplicationStatus.VERIFICATION_FAILED
    assert result.applied is False
    assert result.verification.attempted is True
    assert result.verification.success is False
    assert result.verification.target_resolved is False
    assert _check(result, "target-resolved").passed is False
    assert (root / ".gitignore").read_bytes() == before
    # The record and the backup survive a reverted change, as they must.
    assert result.record_path and Path(result.record_path).is_file()
    assert Path(result.backup_path).is_file()


# --------------------------------------------------------------------------- #
# 6./7. regressions
# --------------------------------------------------------------------------- #


def test_a_new_error_fails_even_though_the_target_went_away(
    make_project, tmp_path, monkeypatch
):
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    before = (root / ".gitignore").read_bytes()

    # One real scan, plus one finding that a real change to a real project could
    # plausibly produce. The scan itself is not stubbed.
    original = fix_apply.build_report

    def with_error(facts):
        report = original(facts)
        if "dist/" in (root / ".gitignore").read_text(encoding="utf-8"):
            import dataclasses

            from reprocheck.fix.models import utc_now  # noqa: F401
            from reprocheck.models import Finding, Severity

            return dataclasses.replace(
                report,
                findings=[
                    *report.findings,
                    Finding(
                        id="RC999",
                        title="Synthetic",
                        severity=Severity.ERROR,
                        category="python",
                        message="a synthetic error the test added",
                        evidence=".gitignore",
                        file=".gitignore",
                    ),
                ],
            )
        return report

    monkeypatch.setattr(fix_apply, "build_report", with_error)

    result = _apply_verify(root)

    assert result.verification.target_resolved is True
    assert result.verification.success is False
    assert any("new error" in item for item in result.verification.regressions)
    assert result.status is ApplicationStatus.VERIFICATION_FAILED
    assert (root / ".gitignore").read_bytes() == before


def test_a_new_rc220_is_a_regression(make_project, tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    before = (root / ".gitignore").read_bytes()

    original = fix_apply.build_report

    def with_rc220(facts):
        import dataclasses

        from reprocheck.models import Finding, Severity

        report = original(facts)
        if "dist/" in (root / ".gitignore").read_text(encoding="utf-8"):
            return dataclasses.replace(
                report,
                findings=[
                    *report.findings,
                    Finding(
                        id="RC220",
                        title="Synthetic unpinned action",
                        severity=Severity.WARNING,
                        category="ci",
                        message="a synthetic RC220 the test added",
                        evidence=".github/workflows/ci.yml",
                        file=".github/workflows/ci.yml",
                    ),
                ],
            )
        return report

    monkeypatch.setattr(fix_apply, "build_report", with_rc220)

    result = _apply_verify(root)

    assert result.verification.target_resolved is True
    assert result.verification.success is False
    assert any("RC220" in item for item in result.verification.regressions)
    assert (root / ".gitignore").read_bytes() == before


def test_a_new_rc221_is_a_regression(make_project, tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    original = fix_apply.build_report

    def with_rc221(facts):
        import dataclasses

        from reprocheck.models import Finding, Severity

        report = original(facts)
        if "dist/" in (root / ".gitignore").read_text(encoding="utf-8"):
            return dataclasses.replace(
                report,
                findings=[
                    *report.findings,
                    Finding(
                        id="RC221",
                        title="Synthetic drift",
                        severity=Severity.WARNING,
                        category="ci",
                        message="a synthetic RC221 the test added",
                        evidence="actions/checkout",
                        file=".github/workflows/ci.yml",
                    ),
                ],
            )
        return report

    monkeypatch.setattr(fix_apply, "build_report", with_rc221)

    result = _apply_verify(root)

    assert result.verification.success is False
    assert any("RC221" in item for item in result.verification.regressions)


def test_an_unrelated_new_warning_is_not_a_regression(
    make_project, tmp_path, monkeypatch
):
    # Policy, stated as a test: a new finding somewhere else, at warning
    # severity, is what a fix is allowed to cause incidentally and is not a
    # reason to revert somebody's work.
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    before = (root / ".gitignore").read_bytes()
    # Captured while the finding still exists, which is the only moment the
    # suggestion can be asked what it would write.
    proposed = _proposed_bytes(root)

    original = fix_apply.build_report

    def with_warning(facts):
        import dataclasses

        from reprocheck.models import Finding, Severity

        report = original(facts)
        if "dist/" in (root / ".gitignore").read_text(encoding="utf-8"):
            return dataclasses.replace(
                report,
                findings=[
                    *report.findings,
                    Finding(
                        id="RC998",
                        title="Synthetic warning elsewhere",
                        severity=Severity.WARNING,
                        category="python",
                        message="a synthetic warning in another file",
                        evidence="README.md",
                        file="README.md",
                    ),
                ],
            )
        return report

    monkeypatch.setattr(fix_apply, "build_report", with_warning)

    result = _apply_verify(root)

    assert result.verification.success is True
    assert result.status is ApplicationStatus.APPLIED
    assert result.verification.added  # it was noticed and not treated as fatal
    assert result.verification.regressions == ()
    # Compared against what the suggestion proposed, not a literal: the rule
    # keeps the line endings of the file it read, so the bytes are CRLF on
    # Windows and LF on Linux, and a test that hardcodes either one is testing
    # the platform.
    assert (root / ".gitignore").read_bytes() == proposed
    assert (root / ".gitignore").read_bytes() != before


def test_a_line_that_moved_is_not_a_new_finding() -> None:
    """The identity must ignore line numbers, or every insertion looks new."""
    before = {
        "findings": [
            {
                "id": "RC120",
                "file": "a.py",
                "evidence": "a.py:10; a.py:20",
                "message": "hardcoded path",
                "severity": "warning",
            }
        ]
    }
    after = {
        "findings": [
            {
                "id": "RC120",
                "file": "a.py",
                "evidence": "a.py:12; a.py:22",
                "message": "hardcoded path",
                "severity": "warning",
            }
        ]
    }

    assert set(identities(before)) == set(identities(after))
    assert (
        verify_application(
            root=Path("."),
            before=before,
            after=after,
            target_file="a.py",
            target_finding_id="RC120",
            expected_sha256="",
            allowed_paths=frozenset({"a.py"}),
            before_snapshot=None,
            after_snapshot=None,
        ).added
        == ()
    )


# --------------------------------------------------------------------------- #
# 8./9./10. rollback
# --------------------------------------------------------------------------- #


def test_a_failed_verification_reverts_and_the_bytes_match_exactly(
    make_project, tmp_path, monkeypatch
):
    _state(tmp_path, monkeypatch)
    root = _project(make_project, gitignore="*.log\n# a comment without newline")
    before = (root / ".gitignore").read_bytes()

    _during_rescan(monkeypatch, lambda _root: _inject_error(monkeypatch))

    result = _apply_verify(root)

    assert result.status is ApplicationStatus.VERIFICATION_FAILED
    assert result.applied is False
    assert result.rollback is RollbackState.SUCCEEDED
    assert result.verification.success is False
    assert (root / ".gitignore").read_bytes() == before


def test_rollback_preserves_a_users_own_earlier_edit(
    make_project, tmp_path, monkeypatch
):
    """The user had already edited this file. Their bytes must come back."""
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    # The user's own uncommitted change, present before the fix.
    users_bytes = b"__pycache__/\n# added by hand, do not lose this\n"
    (root / ".gitignore").write_bytes(users_bytes)
    assert (root / ".gitignore").read_bytes() == users_bytes

    _during_rescan(monkeypatch, lambda _root: _inject_error(monkeypatch))
    result = _apply_verify(root)

    assert result.verification.success is False
    assert (root / ".gitignore").read_bytes() == users_bytes
    assert b"do not lose this" in (root / ".gitignore").read_bytes()
    # And the backup holds the user's bytes too, not the fix's.
    assert Path(result.backup_path).read_bytes() == users_bytes


def test_a_successful_verification_keeps_the_change(
    make_project, tmp_path, monkeypatch
):
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    result = _apply_verify(root)

    assert result.applied is True
    assert result.rollback is RollbackState.NOT_NEEDED
    assert (root / ".gitignore").read_text(encoding="utf-8") == COMPLETE_IGNORE


def _inject_error(monkeypatch) -> None:
    """Make the post-fix rescan carry one extra error finding."""
    import dataclasses

    from reprocheck.models import Finding, Severity

    original = fix_apply.build_report

    def with_error(facts):
        report = original(facts)
        return dataclasses.replace(
            report,
            findings=[
                *report.findings,
                Finding(
                    id="RC997",
                    title="Synthetic",
                    severity=Severity.ERROR,
                    category="python",
                    message="a synthetic error the test added",
                    evidence="demo.py",
                    file="demo.py",
                ),
            ],
        )

    monkeypatch.setattr(fix_apply, "build_report", with_error)


# --------------------------------------------------------------------------- #
# 12./13. concurrent modification
# --------------------------------------------------------------------------- #


def test_an_external_write_to_the_target_fails_verification(
    make_project, tmp_path, monkeypatch
):
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    def foreign(_root):
        (root / ".gitignore").write_text("__pycache__/\nsomething-else/\n", "utf-8")

    _during_rescan(monkeypatch, foreign)

    result = _apply_verify(root)

    assert result.verification.success is False
    assert _check(result, "content-intact").passed is False
    assert "wrote to it" in (_check(result, "content-intact").detail or "")


def test_a_rollback_that_would_destroy_an_external_change_does_not(
    make_project, tmp_path, monkeypatch
):
    """The worst available outcome is overwriting work that is not ours."""
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    theirs = "build/\ndist/\n# written by somebody else mid-flight\n"

    def foreign(_root):
        (root / ".gitignore").write_text(theirs, "utf-8")

    _during_rescan(monkeypatch, foreign)

    result = _apply_verify(root)

    assert result.verification.success is False
    assert result.rollback is RollbackState.SKIPPED_STALE
    assert result.applied is False
    # Their bytes survive, and the message says where ours are.
    assert (root / ".gitignore").read_text("utf-8") == theirs
    assert "was NOT reverted" in " ".join(result.messages)
    assert Path(result.backup_path).is_file()


def test_a_change_to_another_file_during_verification_fails_it(
    make_project, tmp_path, monkeypatch
):
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    before = (root / ".gitignore").read_bytes()

    def intruder(_root):
        (_root / "README.md").write_text("# changed by something else\n", "utf-8")

    _during_rescan(monkeypatch, intruder)

    result = _apply_verify(root)

    assert _check(result, "project-integrity").passed is False
    assert "README.md" in (_check(result, "project-integrity").detail or "")
    assert result.verification.success is False
    # The target is still restored: the integrity failure is about another file.
    assert (root / ".gitignore").read_bytes() == before


# --------------------------------------------------------------------------- #
# 15. the precondition still guards everything
# --------------------------------------------------------------------------- #


def test_a_stale_precondition_writes_nothing_and_verifies_nothing(
    make_project, tmp_path, monkeypatch
):
    # A suggestion from an older report is never trusted: the one used here is
    # regenerated from the project as it is now, so the precondition can only go
    # stale if the file changes between that scan and the write. Which is the
    # race the precondition exists for, and is reproduced here rather than
    # asserted.
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    from reprocheck.suggest import suggest as real_suggest

    def race(facts, report):
        proposals = real_suggest(facts, report)
        # Somebody else writes, after the proposal was made and before the file
        # is read for the precondition.
        (root / ".gitignore").write_text("__pycache__/\n# theirs\n", "utf-8")
        return proposals

    monkeypatch.setattr(fix_apply, "suggest", race)

    result = _apply_verify(root)

    assert result.status is ApplicationStatus.STALE
    assert result.precondition_passed is False
    assert (root / ".gitignore").read_text("utf-8") == "__pycache__/\n# theirs\n"
    # A verification that never compared anything says so rather than claiming
    # a pass.
    assert result.verification.attempted is False
    assert result.backup_path is None
    assert result.record_path is None
    from reprocheck.cli import _fix_exit_code

    assert _fix_exit_code(result) == EXIT_STALE


# --------------------------------------------------------------------------- #
# 16./18. integrity
# --------------------------------------------------------------------------- #


def test_only_the_proposed_file_may_change(make_project, tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    before = snapshot(root)

    result = _apply_verify(root)

    assert _check(result, "project-integrity").passed is True
    assert set(_changed(before, snapshot(root))) == {".gitignore"}
    assert result.unexpected_paths == ()


def test_the_diff_decides_which_files_may_change() -> None:
    diff = (
        "--- a/.gitignore\n"
        "+++ b/.gitignore\n"
        "@@ -1 +1,2 @@\n"
        " a\n"
        "+b\n"
        "--- a/extra.txt\n"
        "+++ b/extra.txt\n"
        "@@ -0,0 +1 @@\n"
        "+x\n"
    )
    assert diff_paths(diff, ".gitignore") == frozenset({".gitignore", "extra.txt"})
    # No diff at all falls back to the suggestion's own file rather than to
    # "nothing may change", which would let anything through.
    assert diff_paths(None, ".gitignore") == frozenset({".gitignore"})


def test_a_project_without_git_verifies(make_project, tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    assert not (root / ".git").exists()

    result = _apply_verify(root)

    assert result.verification.success is True
    assert result.git_before.is_repository is False
    assert (root / ".gitignore").read_text(encoding="utf-8") == COMPLETE_IGNORE


@pytest.mark.skipif(not GIT_AVAILABLE, reason="Git is not installed")
def test_a_dirty_git_working_tree_is_not_a_reason_to_refuse(
    make_project, tmp_path, monkeypatch
):
    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    run_git(root, "init")
    run_git(root, "add", "-A")
    # The identity is supplied per command: a CI runner has no user.name or
    # user.email configured, and a commit that fails for want of one has
    # nothing to do with what this test is about.
    run_git(
        root,
        "-c",
        "user.email=tests@example.invalid",
        "-c",
        "user.name=ReproCheck tests",
        "commit",
        "-m",
        "initial",
    )
    # The user's own uncommitted work, on another file.
    (root / "README.md").write_text("# mine, uncommitted\n", "utf-8")
    run_git(root, "add", "README.md")

    result = _apply_verify(root)

    assert result.verification.success is True
    assert result.git_before.is_repository is True
    assert (root / "README.md").read_text(encoding="utf-8") == "# mine, uncommitted\n"
    assert (root / ".gitignore").read_text(encoding="utf-8") == COMPLETE_IGNORE


# --------------------------------------------------------------------------- #
# 10. dry run
# --------------------------------------------------------------------------- #


def test_a_dry_run_says_the_verification_was_not_attempted(
    make_project, tmp_path, monkeypatch
):
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    result = run_fix(root, RC140, apply=False, verify=True)

    assert result.status is ApplicationStatus.DRY_RUN
    assert result.verification.attempted is False
    assert result.verification.not_attempted_reason == "dry_run"
    assert (root / ".gitignore").read_text(encoding="utf-8") == "__pycache__/\n"


# --------------------------------------------------------------------------- #
# 22./23./24. reports
# --------------------------------------------------------------------------- #


def test_the_json_carries_a_structured_verification(
    make_project, tmp_path, monkeypatch
):
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    result = _apply_verify(root)
    payload = result.to_dict()["verification"]

    assert payload["attempted"] is True
    assert payload["success"] is True
    assert payload["target_finding_id"] == "RC140"
    assert payload["target_before"] == 1
    assert payload["target_after"] == 0
    assert payload["regressions"] == []
    assert isinstance(payload["checks"], list)
    assert {item["name"] for item in payload["checks"]} == {
        "target-resolved",
        "content-intact",
        "no-regressions",
        "project-integrity",
    }
    assert payload["duration_seconds"] is not None


def test_the_markdown_has_a_verification_section(make_project, tmp_path, monkeypatch):
    from reprocheck.reporters.fix import render_fix_markdown

    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    text = render_fix_markdown(_apply_verify(root))

    assert "## Verification" in text
    assert "Result: **PASS**" in text
    assert "Target finding: RC140" in text
    assert "`target-resolved`: **pass**" in text
    assert "does not run the project's tests" in text


def test_the_markdown_says_when_the_verification_did_not_run(
    make_project, tmp_path, monkeypatch
):
    from reprocheck.reporters.fix import render_fix_markdown

    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    dry = render_fix_markdown(run_fix(root, RC140, apply=False))
    plain = render_fix_markdown(run_fix(root, RC140, apply=True))

    assert "## Verification" in dry
    assert "Reason: `dry_run`" in dry
    assert "Reason: `--verify` was not given" in plain


def test_the_terminal_shows_the_block(make_project, tmp_path, monkeypatch, capsys):
    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    code = main(
        [
            "fix",
            str(root),
            "--suggestion",
            RC140,
            "--apply",
            "--verify",
            "--json",
            str(tmp_path / "fix.json"),
            "--markdown",
            str(tmp_path / "fix.md"),
        ]
    )
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "Verification:" in out
    assert "  target resolved: yes" in out
    assert "  new errors: 0" in out
    assert "  regressions: 0" in out
    assert "  result: PASS" in out


def test_the_audit_record_carries_the_verification(make_project, tmp_path, monkeypatch):
    from reprocheck.fix.records import load_record

    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    result = _apply_verify(root)
    stored = load_record(root, str(result.record_id))

    assert stored is not None
    assert stored.verification.attempted is True
    assert stored.verification.success is True
    assert stored.verification.target_resolved is True
    assert stored.verification.regressions == ()
    assert stored.verification.duration_seconds is not None
    # The rollback fields already existed and were not duplicated inside it.
    assert stored.rollback is RollbackState.NOT_NEEDED


def test_the_audit_record_carries_a_failed_verification(
    make_project, tmp_path, monkeypatch
):
    from reprocheck.fix.records import load_record

    _state(tmp_path, monkeypatch)
    root = _project(make_project)
    _during_rescan(monkeypatch, lambda _root: _inject_error(monkeypatch))

    result = _apply_verify(root)
    stored = load_record(root, str(result.record_id))

    assert stored is not None
    assert stored.verification.attempted is True
    assert stored.verification.success is False
    assert stored.verification.regressions
    assert stored.rollback is RollbackState.SUCCEEDED
    assert stored.rollback_reason


def test_an_old_record_without_verification_still_loads() -> None:
    """A record written before this field existed must still be readable."""
    from reprocheck.fix.models import ApplicationRecord

    old = {
        "record_id": "20260101T000000-FIX-RC140-001",
        "project_identity": "demo",
        "project_path": "/tmp/demo",
        "suggestion_id": "FIX-RC140-001",
        "finding_id": "RC140",
        "safety": "SAFE",
        "file": ".gitignore",
        "applied_at": "2026-01-01T00:00:00+00:00",
        "before_sha256": "a" * 64,
        "after_sha256": "b" * 64,
        "backup_path": "/tmp/before.bin",
        "dry_run": False,
        "success": True,
        "rollback": "NOT_NEEDED",
        "reprocheck_version": "0.0.1",
        "status": "APPLIED",
        "reason": "the proposed content was written and validated",
    }

    record = ApplicationRecord.from_dict(old)

    assert record.record_id == old["record_id"]
    assert record.status is ApplicationStatus.APPLIED
    assert record.verification.attempted is False
    assert record.verification.not_attempted_reason is None
    # And it round-trips with the field present but empty.
    again = ApplicationRecord.from_dict(record.to_dict())
    assert again.verification == record.verification


# --------------------------------------------------------------------------- #
# 16. exit codes
# --------------------------------------------------------------------------- #


def test_the_exit_codes_are_the_ones_that_already_existed(
    make_project, tmp_path, monkeypatch
):
    from reprocheck.cli import EXIT_ROLLBACK_FAILED, _fix_exit_code

    _state(tmp_path, monkeypatch)
    # A fresh project per case: applying a fix resolves the finding, so a second
    # run on the same tree would be refused for a different reason entirely.
    assert _fix_exit_code(_apply_verify(_project(make_project, name="ok"))) == EXIT_OK
    assert (
        _fix_exit_code(run_fix(_project(make_project, name="dry"), RC140, apply=False))
        == EXIT_OK
    )

    # A verification that fails and reverts is 6, the code that already meant
    # "ReproCheck did not leave the change in place", and not a new one.
    failing = _project(make_project, name="failing")
    _during_rescan(monkeypatch, lambda _root: _inject_error(monkeypatch))
    failed = _apply_verify(failing)
    assert failed.status is ApplicationStatus.VERIFICATION_FAILED
    assert _fix_exit_code(failed) == EXIT_ROLLED_BACK
    # A suggestion that is gone or no longer applicable is 5, as it was
    # before, and a dry run is 0.
    assert _fix_exit_code(run_fix(failing, "FIX-RC140-999", apply=True)) == EXIT_STALE

    severe = failed.__class__(
        status=ApplicationStatus.ROLLBACK_FAILED,
        reprocheck_version=failed.reprocheck_version,
    )
    assert _fix_exit_code(severe) == EXIT_ROLLBACK_FAILED
    assert EXIT_STALE != EXIT_ROLLED_BACK


# --------------------------------------------------------------------------- #
# 22./23./25. encodings, line endings, paths
# --------------------------------------------------------------------------- #


def test_a_file_with_crlf_and_no_trailing_newline(make_project, tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    root = make_project(
        _files(gitignore="__pycache__/\r\nbuild/\r\n# no trailing newline"),
        name="demo",
    )
    original = (root / ".gitignore").read_bytes()

    result = _apply_verify(root)

    assert result.verification.success is True
    written = (root / ".gitignore").read_bytes()
    assert written != original
    # The user's bytes come back exactly, CRLF and missing newline included.
    from reprocheck.fix import run_rollback

    run_rollback(root, str(result.record_id))
    assert (root / ".gitignore").read_bytes() == original


def test_a_file_with_non_ascii_content(make_project, tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    root = make_project(
        _files(
            gitignore="# não remova isto\n__pycache__/\n", extra={"LEIAME.md": "áéí\n"}
        ),
        name="demo",
    )
    original = (root / ".gitignore").read_bytes()

    result = _apply_verify(root)

    assert result.verification.success is True
    assert "não remova isto" in (root / ".gitignore").read_text(encoding="utf-8")

    from reprocheck.fix import run_rollback

    run_rollback(root, str(result.record_id))
    assert (root / ".gitignore").read_bytes() == original


def test_a_path_with_spaces(make_project, tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    root = make_project(_files(), name="a project with spaces")

    result = _apply_verify(root)

    assert result.verification.success is True
    assert (root / ".gitignore").read_text(encoding="utf-8") == COMPLETE_IGNORE


# --------------------------------------------------------------------------- #
# 20./28. what verification is not
# --------------------------------------------------------------------------- #


def test_verification_never_runs_project_code(make_project, tmp_path, monkeypatch):
    """A project that would fail loudly if anything were executed."""
    _state(tmp_path, monkeypatch)
    root = _project(
        make_project,
        extra={
            "conftest.py": "raise SystemExit('project code was executed')\n",
            "demo.py": "raise SystemExit('project code was executed')\n",
        },
    )

    result = _apply_verify(root)

    assert result.verification.success is True


def test_verification_makes_no_network_call(make_project, tmp_path, monkeypatch):
    """Proved by construction: the socket is replaced by one that fails."""
    import socket

    def refuse(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("--verify opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    root = _project(make_project)
    result = _apply_verify(root)

    assert result.verification.success is True


def test_a_review_required_suggestion_is_still_refused(
    make_project, tmp_path, monkeypatch
):
    """--verify changes nothing about who may be applied."""
    from reprocheck.fix import apply as apply_module

    _state(tmp_path, monkeypatch)
    root = _project(make_project)

    from reprocheck.suggest import suggest as real_suggest

    def demote(facts, report):
        import dataclasses

        proposals = real_suggest(facts, report)
        return dataclasses.replace(
            proposals,
            suggestions=[
                dataclasses.replace(item, safety=Safety.REVIEW_REQUIRED)
                if item.suggestion_id == RC140
                else item
                for item in proposals.suggestions
            ],
        )

    monkeypatch.setattr(apply_module, "suggest", demote)

    result = _apply_verify(root)

    assert result.status is ApplicationStatus.NOT_APPLICABLE
    assert "not SAFE" in result.reason
    assert (root / ".gitignore").read_text(encoding="utf-8") == "__pycache__/\n"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _changed(before, after) -> set[str]:
    """Files that appeared, disappeared or changed size between snapshots."""
    found = set()
    for path in set(before) | set(after):
        old = before.get(path)
        new = after.get(path)
        if old is None or new is None or old[0] != new[0]:
            found.add(path)
    return found


def test_the_helpers_used_above_are_the_real_ones() -> None:
    """Guards the two helpers this file leans on, so a bug in them is visible."""
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "a.txt").write_text("one", encoding="utf-8")
        first = snapshot_project(root)
        (root / "a.txt").write_text("two", encoding="utf-8")
        (root / "b.txt").write_text("new", encoding="utf-8")
        second = snapshot_project(root)

        assert first.changed_since(second, allowed=frozenset()) == ("a.txt", "b.txt")
        assert first.changed_since(second, allowed=frozenset({"a.txt"})) == ("b.txt",)


def test_the_verification_model_round_trips() -> None:
    from reprocheck.fix.models import VerificationCheck

    original = FixVerification(
        attempted=True,
        success=False,
        target_finding_id="RC140",
        target_before=1,
        target_after=1,
        findings_before=6,
        findings_after=6,
        removed=("RC140|.gitignore|.gitignore",),
        added=("RC999|demo.py|demo.py",),
        regressions=("new error: RC999 demo.py",),
        checks=(
            VerificationCheck("target-resolved", False, "still reported"),
            VerificationCheck("content-intact", True, None),
        ),
        duration_seconds=0.5,
    )
    again = FixVerification.from_dict(original.to_dict())

    assert again.attempted is True
    assert again.success is False
    assert again.target_finding_id == "RC140"
    assert again.removed == original.removed
    assert again.added == original.added
    assert again.regressions == original.regressions
    assert [c.name for c in again.checks] == ["target-resolved", "content-intact"]
    assert again.checks[0].passed is False
    assert again.checks[1].detail is None
    # A record written before the field existed loads as "never attempted"
    # rather than raising.
    assert FixVerification.from_dict(None).attempted is False
    assert FixVerification.from_dict({}).attempted is False
