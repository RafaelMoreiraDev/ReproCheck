"""Tests for baselines, the comparison engine and the output-location policy.

Every test here works on report *documents*, the way the CLI does: nothing
reaches the network, nothing executes the analysed project, and the comparison
never touches a file beyond the destinations it was given.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

from conftest import (
    dependency_declaration,
    finding,
    report_document,
    reproduction_document,
    workflow_reference,
)
from reprocheck.cli import EXIT_OK, EXIT_OPERATIONAL, main
from reprocheck.diff import (
    BaselineError,
    ChangeKind,
    IdentityError,
    compare_reports,
    ensure_same_project,
    load_baseline,
    load_report,
    save_baseline,
)
from reprocheck.diff.models import DIFF_SCHEMA_VERSION
from reprocheck.diff.normalise import normalise_findings
from reprocheck.output import (
    BASELINE_NAME,
    DIFF_NAME,
    REPORT_NAME,
    project_identity,
    project_output_dir,
    state_dir,
)
from reprocheck.reporters.diff import render_diff_markdown, write_diff_json
from reprocheck.scanner import REPORT_SCHEMA_VERSION

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _write(path: Path, document: dict) -> Path:
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def _diff(baseline: dict, current: dict):
    return compare_reports(baseline, current)


def _baseline_file(tmp_path: Path, report: dict, name: str = "baseline.json") -> Path:
    return save_baseline(copy.deepcopy(report), tmp_path / name)


# --------------------------------------------------------------------------- #
# 1./2./3. volatile fields produce no change
# --------------------------------------------------------------------------- #


def test_only_the_timestamp_changed() -> None:
    baseline = report_document(reproduction=reproduction_document(), verdict="PASS")
    current = report_document(
        timestamp="2026-06-30T23:59:59+00:00",
        reproduction=reproduction_document(),
        verdict="PASS",
    )

    assert _diff(baseline, current).material_change_count == 0


def test_durations_and_workspace_paths_are_ignored() -> None:
    baseline = report_document(reproduction=reproduction_document(duration=12.5))
    current = report_document(
        reproduction=reproduction_document(
            duration=287.75,
            workspace="D:/other-temp/reprocheck/20991231-235959-deadbeef",
        )
    )
    diff = _diff(baseline, current)

    assert diff.material_change_count == 0
    assert not diff.has_changes


def test_reproduction_timestamp_noise_is_ignored() -> None:
    baseline = report_document(reproduction=reproduction_document())
    current = report_document(reproduction=reproduction_document())
    current["reproduction"]["completed_steps"] = ["a", "b", "c", "d", "e", "f", "g"]
    current["reproduction"]["installation"]["stdout_snippet"] = (
        "a totally different pip text"
    )
    current["reproduction"]["python"]["reason"] = (
        "Interpreters found locally: 3.9, 3.14"
    )
    current["reproduction"]["venv"]["path"] = "C:/elsewhere/venv"

    assert _diff(baseline, current).material_change_count == 0


def test_finding_line_numbers_are_ignored() -> None:
    """A line shift renumbers every finding below it; it is not a new problem."""
    baseline = report_document(findings=[finding(line=7)])
    current = report_document(findings=[finding(line=31)])

    assert _diff(baseline, current).material_change_count == 0


def test_comparison_is_deterministic() -> None:
    baseline = report_document(
        findings=[finding()], reproduction=reproduction_document()
    )
    current = report_document(
        verdict="PARTIAL",
        findings=[finding("RC220", evidence="ci.yml:12")],
        reproduction=reproduction_document(version="2.0.0", modules=("api", "cli")),
    )

    first = _diff(baseline, current).to_dict()
    second = _diff(baseline, current).to_dict()
    assert json.dumps(first) == json.dumps(second)


# --------------------------------------------------------------------------- #
# 4. verdict change
# --------------------------------------------------------------------------- #


def test_verdict_change_is_stated_factly() -> None:
    diff = _diff(report_document(verdict="PASS"), report_document(verdict="PARTIAL"))

    assert diff.verdict.changed is True
    assert (diff.verdict.previous, diff.verdict.current) == ("PASS", "PARTIAL")
    assert "better" not in json.dumps(diff.to_dict())
    assert "worse" not in json.dumps(diff.to_dict())


def test_verdict_without_change_is_not_a_material_change() -> None:
    diff = _diff(report_document(verdict="PARTIAL"), report_document(verdict="PARTIAL"))

    assert diff.verdict.changed is False
    assert diff.material_change_count == 0


def test_not_attempted_to_partial() -> None:
    diff = _diff(
        report_document(verdict="NOT_ATTEMPTED"),
        report_document(
            verdict="PARTIAL", reproduction=reproduction_document(runtime_checks=False)
        ),
    )

    assert (diff.verdict.previous, diff.verdict.current) == ("NOT_ATTEMPTED", "PARTIAL")


# --------------------------------------------------------------------------- #
# 5./6./7. findings
# --------------------------------------------------------------------------- #


def test_added_finding() -> None:
    diff = _diff(report_document(), report_document(findings=[finding()]))

    assert len(diff.findings.added) == 1
    assert not diff.findings.resolved
    assert diff.findings.added[0].kind is ChangeKind.ADDED
    assert diff.findings.added[0].current["id"] == "RC203"


def test_resolved_finding() -> None:
    diff = _diff(report_document(findings=[finding()]), report_document())

    assert len(diff.findings.resolved) == 1
    assert diff.findings.resolved[0].previous["id"] == "RC203"


def test_aggregated_finding_with_a_new_subject_is_resolved_plus_added() -> None:
    """RC203 covers a set of packages: changing the set is two facts, not one."""
    baseline = report_document(
        findings=[
            finding(
                message="2 runtime dependencies have no version constraint: httpx, requests.",
                evidence="httpx; requests",
            )
        ]
    )
    current = report_document(
        findings=[
            finding(
                message="1 runtime dependency has no version constraint: httpx.",
                evidence="httpx",
            )
        ]
    )
    diff = _diff(baseline, current)

    assert len(diff.findings.resolved) == 1
    assert len(diff.findings.added) == 1
    assert not diff.findings.changed
    assert "requests" in diff.findings.resolved[0].previous["evidence"]
    assert diff.findings.added[0].current["evidence"] == "httpx"


def test_materially_changed_finding() -> None:
    """Same identity, different content: an edit, not a resolution."""
    baseline = report_document(
        findings=[
            finding(
                "RC201",
                "'x' requires 'y==1.0', but 'z' requires 'y==2.0'.",
                evidence="pyproject.toml:7; pyproject.toml:9",
                severity="error",
            )
        ]
    )
    current = report_document(
        findings=[
            finding(
                "RC201",
                "'x' requires 'y==1.1', but 'z' requires 'y==2.0'.",
                evidence="pyproject.toml:7; pyproject.toml:9",
                severity="error",
            )
        ]
    )
    diff = _diff(baseline, current)

    assert not diff.findings.added
    assert not diff.findings.resolved
    assert len(diff.findings.changed) == 1
    assert diff.findings.changed[0].changed_fields == ("message",)


def test_a_moved_file_is_a_new_finding() -> None:
    diff = _diff(
        report_document(findings=[finding(file="pyproject.toml")]),
        report_document(findings=[finding(file="setup.cfg")]),
    )

    assert len(diff.findings.added) == 1
    assert len(diff.findings.resolved) == 1


def test_finding_identity_ignores_the_message_wording() -> None:
    baseline = report_document(findings=[finding()])
    current = report_document(findings=[finding()])
    current["findings"][0]["message"] = "a completely different sentence"

    # The message is a compared attribute, so this is a change, not a new fact.
    diff = _diff(baseline, current)
    assert diff.findings.changed[0].changed_fields == ("message",)
    assert len(normalise_findings(baseline)) == 1


# --------------------------------------------------------------------------- #
# 8./9./10. dependencies, Python, CI
# --------------------------------------------------------------------------- #


def test_dependency_added_and_removed() -> None:
    diff = _diff(
        report_document(declarations=[dependency_declaration("numpy", "==1.23.5")]),
        report_document(
            declarations=[
                dependency_declaration("numpy", "==1.23.5"),
                dependency_declaration("httpx", "==0.27.0"),
            ]
        ),
    )

    assert [item.kind for item in diff.dependencies] == [ChangeKind.ADDED]
    assert diff.dependencies[0].key.startswith("httpx")
    assert "==0.27.0" in (diff.dependencies[0].current or "")

    removed = _diff(
        report_document(
            declarations=[
                dependency_declaration("numpy", "==1.23.5"),
                dependency_declaration("httpx", "==0.27.0"),
            ]
        ),
        report_document(declarations=[dependency_declaration("numpy", "==1.23.5")]),
    )
    assert [item.kind for item in removed.dependencies] == [ChangeKind.RESOLVED]


def test_dependency_specifier_change_is_an_edit() -> None:
    diff = _diff(
        report_document(declarations=[dependency_declaration("numpy", "==1.23.5")]),
        report_document(declarations=[dependency_declaration("numpy", ">=1.26,<2")]),
    )

    assert len(diff.dependencies) == 1
    change = diff.dependencies[0]
    assert change.kind is ChangeKind.CHANGED
    assert change.changed_fields == ("specifier",)
    assert change.previous == "specifier===1.23.5"
    assert change.current == "specifier=>=1.26,<2"


def test_dependency_vcs_ref_change_is_an_edit() -> None:
    diff = _diff(
        report_document(
            declarations=[
                dependency_declaration(
                    "demo",
                    reference="https://git/repo",
                    reference_kind="vcs",
                    vcs_ref="main",
                )
            ]
        ),
        report_document(
            declarations=[
                dependency_declaration(
                    "demo",
                    reference="https://git/repo",
                    reference_kind="vcs",
                    vcs_ref="a1b2c3",
                    vcs_commit="a1b2c3",
                )
            ]
        ),
    )

    assert diff.dependencies[0].kind is ChangeKind.CHANGED
    assert "vcs_ref" in diff.dependencies[0].changed_fields


def test_dependency_group_move_is_resolved_plus_added() -> None:
    diff = _diff(
        report_document(
            declarations=[dependency_declaration("httpx", "==0.27.0", group="dev")]
        ),
        report_document(
            declarations=[dependency_declaration("httpx", "==0.27.0", group="project")]
        ),
    )

    assert sorted(item.kind.value for item in diff.dependencies) == [
        "added",
        "resolved",
    ]


def test_python_requirement_change_is_an_edit() -> None:
    baseline = report_document(
        python_requirements=[
            {
                "source": "pyproject.toml",
                "value": ">=3.11",
                "file": "pyproject.toml",
                "line": 5,
            }
        ]
    )
    current = report_document(
        python_requirements=[
            {
                "source": "pyproject.toml",
                "value": ">=3.12",
                "file": "pyproject.toml",
                "line": 5,
            }
        ]
    )
    diff = _diff(baseline, current)

    assert len(diff.python) == 1
    assert diff.python[0].kind is ChangeKind.CHANGED
    assert diff.python[0].changed_fields == ("value",)
    assert "value=>=3.11" in (diff.python[0].previous or "")
    assert "value=>=3.12" in (diff.python[0].current or "")


def test_ci_ref_change_is_an_edit() -> None:
    sha = "0" * 40
    diff = _diff(
        report_document(
            workflow_references=[workflow_reference("actions/checkout", "v4")]
        ),
        report_document(
            workflow_references=[
                workflow_reference("actions/checkout", sha, is_sha=True)
            ]
        ),
    )

    assert len(diff.ci) == 1
    change = diff.ci[0]
    assert change.kind is ChangeKind.CHANGED
    assert change.changed_fields == ("immutability", "ref")
    assert change.previous == "immutability=mutable reference, ref=v4"
    assert "full commit SHA" in (change.current or "")


def test_ci_reference_added_and_removed() -> None:
    diff = _diff(
        report_document(
            workflow_references=[workflow_reference("actions/checkout", "v4")]
        ),
        report_document(
            workflow_references=[
                workflow_reference("actions/checkout", "v4"),
                workflow_reference("actions/setup-python", "v5"),
            ]
        ),
    )
    assert [item.kind for item in diff.ci] == [ChangeKind.ADDED]

    removed = _diff(
        report_document(
            workflow_references=[
                workflow_reference("actions/checkout", "v4"),
                workflow_reference("actions/setup-python", "v5"),
            ]
        ),
        report_document(
            workflow_references=[workflow_reference("actions/checkout", "v4")]
        ),
    )
    assert [item.kind for item in removed.ci] == [ChangeKind.RESOLVED]


# --------------------------------------------------------------------------- #
# 11. reproduction
# --------------------------------------------------------------------------- #


def test_installed_version_change() -> None:
    diff = _diff(
        report_document(
            reproduction=reproduction_document(version="0.0.1", fallback=True)
        ),
        report_document(reproduction=reproduction_document(version="1.2.12")),
    )
    changes = {item.key: item for item in diff.reproduction}

    assert changes["installed_distribution.version"].kind is ChangeKind.CHANGED
    assert "value=0.0.1" in (changes["installed_distribution.version"].previous or "")
    assert "value=1.2.12" in (changes["installed_distribution.version"].current or "")
    assert (
        changes["installed_distribution.looks_like_fallback"].kind is ChangeKind.CHANGED
    )


def test_pip_check_clean_to_conflict() -> None:
    diff = _diff(
        report_document(reproduction=reproduction_document()),
        report_document(
            reproduction=reproduction_document(pip_clean=False, conflicts=2)
        ),
    )
    changes = {item.key: item for item in diff.reproduction}

    assert changes["pip_check.clean"].changed_fields == ("value",)
    assert changes["pip_check.conflict_count"].kind is ChangeKind.CHANGED


def test_installation_failure() -> None:
    diff = _diff(
        report_document(reproduction=reproduction_document()),
        report_document(reproduction=reproduction_document(install_success=False)),
    )
    changes = {item.key: item for item in diff.reproduction}

    assert changes["installation.success"].current == "value=false"


def test_imports_added_removed_and_failed() -> None:
    diff = _diff(
        report_document(
            reproduction=reproduction_document(modules=("api", "dashboards", "cli"))
        ),
        report_document(
            reproduction=reproduction_document(modules=("api", "cli"), failing=("api",))
        ),
    )
    changes = {item.key: item for item in diff.reproduction}

    assert changes["imports.dashboards"].kind is ChangeKind.RESOLVED
    assert changes["imports.api"].kind is ChangeKind.CHANGED
    assert changes["imports.api"].previous == "imported=true"
    assert changes["imports.api"].current == "imported=false"


def test_runtime_checks_and_collection_changes() -> None:
    diff = _diff(
        report_document(
            reproduction=reproduction_document(
                runtime_checks=True, collection_available=False
            )
        ),
        report_document(
            reproduction=reproduction_document(
                runtime_checks=True, collection_available=True, collection_collected=42
            )
        ),
    )
    keys = {item.key for item in diff.reproduction}

    assert "pytest_collection.available" in keys
    assert "pytest_collection.collected" in keys


def test_reproduction_added_where_there_was_none() -> None:
    diff = _diff(
        report_document(), report_document(reproduction=reproduction_document())
    )

    assert [item.kind for item in diff.reproduction] == [ChangeKind.ADDED]
    assert diff.reproduction[0].key == "attempted"


def test_reproduction_removed() -> None:
    diff = _diff(
        report_document(reproduction=reproduction_document()), report_document()
    )

    assert [item.kind for item in diff.reproduction] == [ChangeKind.RESOLVED]


def test_selected_python_change() -> None:
    diff = _diff(
        report_document(reproduction=reproduction_document(python="3.11.16")),
        report_document(reproduction=reproduction_document(python="3.12.3")),
    )
    changes = {item.key: item for item in diff.reproduction}

    assert changes["python.selected"].current == "value=3.12.3"


def test_original_project_modified() -> None:
    diff = _diff(
        report_document(reproduction=reproduction_document()),
        report_document(reproduction=reproduction_document(unchanged=False)),
    )
    changes = {item.key: item for item in diff.reproduction}

    assert changes["integrity.unchanged"].kind is ChangeKind.CHANGED
    assert changes["integrity.changed_paths"].kind is ChangeKind.CHANGED


# --------------------------------------------------------------------------- #
# 12. JSON and Markdown
# --------------------------------------------------------------------------- #


def test_diff_json_shape(tmp_path) -> None:
    diff = _diff(
        report_document(verdict="PASS"),
        report_document(verdict="PARTIAL", findings=[finding()]),
    )
    written = write_diff_json(diff, tmp_path / "diff.json")
    data = json.loads(written.read_text(encoding="utf-8"))

    assert data["diff_schema_version"] == DIFF_SCHEMA_VERSION
    assert set(data) == {
        "diff_schema_version",
        "baseline",
        "current",
        "verdict_change",
        "findings",
        "dependencies",
        "python",
        "ci",
        "reproduction",
        "material_change_count",
    }
    assert set(data["findings"]) == {"added", "resolved", "changed"}
    assert data["material_change_count"] == 2


def test_markdown_omits_empty_sections() -> None:
    markdown = render_diff_markdown(_diff(report_document(), report_document()))

    assert "# ReproCheck Baseline Comparison" in markdown
    assert "## Summary" in markdown
    assert "## Verdict" not in markdown
    assert "## New problems" not in markdown
    assert "## Resolved problems" not in markdown
    assert "## Changed facts" not in markdown
    assert "## Technical details" in markdown
    assert "Material changes: 0" in markdown


def test_markdown_shows_evidence_and_the_facts_that_changed() -> None:
    diff = _diff(
        report_document(
            reproduction=reproduction_document(version="0.0.1"),
            declarations=[dependency_declaration("numpy", "==1.23.5")],
            workflow_references=[workflow_reference("actions/checkout", "v4")],
        ),
        report_document(
            verdict="PARTIAL",
            findings=[finding()],
            reproduction=reproduction_document(version="1.2.12"),
            declarations=[dependency_declaration("numpy", ">=1.26")],
            workflow_references=[
                workflow_reference(
                    "actions/checkout", "v4", ".github/workflows/ci.yml", 99
                )
            ],
        ),
    )
    markdown = render_diff_markdown(diff)

    assert "## New problems" in markdown
    assert "RC203 — NEW IN THE COMPARISON" in markdown
    assert "'requests' is declared without a version bound." in markdown
    assert "### Reproduction" in markdown
    assert "installed version" in markdown
    assert "### Dependencies" in markdown
    assert "specifier===1.23.5" in markdown
    assert "— specifier:" in markdown
    assert "## Verdict" in markdown
    assert "The verdict changed from NOT_ATTEMPTED to PARTIAL." in markdown
    assert "does not classify this movement" in markdown
    assert "### CI" not in markdown


def test_markdown_is_deterministic() -> None:
    diff = _diff(
        report_document(findings=[finding()]),
        report_document(verdict="PARTIAL", findings=[finding()]),
    )

    assert render_diff_markdown(diff) == render_diff_markdown(diff)


# --------------------------------------------------------------------------- #
# 13./14. baseline validation and schema policy
# --------------------------------------------------------------------------- #


def test_baseline_is_saved_and_reloaded(tmp_path) -> None:
    report = report_document()
    path = _baseline_file(tmp_path, report)

    assert path.is_file()
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["baseline_schema_version"] == "1"
    assert document["report_schema_version"] == REPORT_SCHEMA_VERSION
    assert document["report"] == report
    assert document["project"]["name"] == "demo"
    assert document["saved_at"]
    assert load_baseline(path).report == report


def test_baseline_refuses_to_overwrite_without_force(tmp_path) -> None:
    path = _baseline_file(tmp_path, report_document())

    with pytest.raises(BaselineError) as excinfo:
        save_baseline(report_document(), path)
    assert "--force" in str(excinfo.value)

    replaced = save_baseline(
        report_document(timestamp="2026-02-02T00:00:00+00:00"), path, force=True
    )
    assert replaced == path
    assert load_baseline(path).identity.scan_timestamp == "2026-02-02T00:00:00+00:00"


def test_invalid_json_is_an_operational_error(tmp_path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(BaselineError) as excinfo:
        load_baseline(path)
    assert "not valid JSON" in str(excinfo.value)


def test_json_that_is_not_a_report_is_refused(tmp_path) -> None:
    path = _write(tmp_path / "other.json", {"hello": "world"})

    with pytest.raises(BaselineError) as excinfo:
        load_baseline(path)
    assert "not a ReproCheck report" in str(excinfo.value)


def test_report_without_findings_list_is_refused(tmp_path) -> None:
    document = report_document()
    document["findings"] = {}
    path = _write(tmp_path / "r.json", document)

    with pytest.raises(BaselineError) as excinfo:
        load_report(path)
    assert "'findings' must be a list" in str(excinfo.value)


def test_unknown_schema_is_refused(tmp_path) -> None:
    path = _write(tmp_path / "future.json", report_document(schema="99"))

    with pytest.raises(BaselineError) as excinfo:
        load_report(path)
    assert "report schema '99'" in str(excinfo.value)
    assert "Re-run ReproCheck" in str(excinfo.value)


def test_unknown_baseline_schema_is_refused(tmp_path) -> None:
    document = {
        "baseline_schema_version": "7",
        "report": report_document(),
    }
    path = _write(tmp_path / "b.json", document)

    with pytest.raises(BaselineError) as excinfo:
        load_baseline(path)
    assert "baseline schema '7'" in str(excinfo.value)


def test_older_schema_is_accepted_and_reported_as_absent(tmp_path) -> None:
    document = report_document(schema="5")
    del document["verdict"]
    path = _write(tmp_path / "old.json", document)

    baseline = load_baseline(path)
    assert baseline.report_schema_version == "5"
    assert baseline.absent_sections() == ("verdict",)

    diff = compare_reports(document, report_document())
    assert diff.verdict.previous is None
    assert diff.verdict.changed is True
    assert "predates the verdict model" in (diff.verdict.note or "")


def test_bare_report_is_accepted_as_a_baseline(tmp_path) -> None:
    path = _write(tmp_path / "bare.json", report_document())

    baseline = load_baseline(path)

    assert baseline.identity.name == "demo"
    assert baseline.saved_at is None


# --------------------------------------------------------------------------- #
# 15b. project identity
# --------------------------------------------------------------------------- #


def test_same_project_compares(tmp_path) -> None:
    baseline = _baseline_file(tmp_path, report_document())

    diff = compare_reports(load_baseline(baseline).report, report_document())

    assert diff.material_change_count == 0


def test_different_projects_are_refused() -> None:
    other = report_document()
    other["project"]["path"] = "C:/tmp/other-project"

    with pytest.raises(IdentityError) as excinfo:
        ensure_same_project(report_document(), other)

    message = str(excinfo.value)
    assert "two different projects" in message
    assert "C:/tmp/demo" in message
    assert "C:/tmp/other-project" in message
    assert "override" not in message.lower()


def test_identity_ignores_the_project_name_but_not_the_path() -> None:
    renamed = report_document()
    renamed["project"]["name"] = "another-name"
    renamed["project"]["path"] = "C:/tmp/demo"  # same directory

    ensure_same_project(report_document(), renamed)


def test_identity_is_case_insensitive_on_windows() -> None:
    """Case folding is a Windows fact, not a portability choice.

    The identity used to fold case on every platform, which made the test suite
    pass on Linux while asserting something untrue about POSIX paths.
    """
    if sys.platform != "win32":
        pytest.skip("case folding is only applied on Windows")
    other = report_document()
    other["project"]["path"] = "C:\\TMP\\Demo"

    ensure_same_project(report_document(), other)


def test_identity_is_case_sensitive_on_posix() -> None:
    """``/srv/Data`` and ``/srv/data`` are two different projects on Linux."""
    if sys.platform == "win32":
        pytest.skip("Windows paths are case insensitive")
    first = report_document()
    first["project"]["path"] = "/srv/Data"
    second = report_document()
    second["project"]["path"] = "/srv/data"

    with pytest.raises(IdentityError):
        ensure_same_project(first, second)


def test_identity_ignores_the_git_head() -> None:
    other = report_document()
    other["git"]["head"] = "f" * 40

    ensure_same_project(report_document(), other)


def test_report_without_a_path_is_refused() -> None:
    anonymous = report_document()
    anonymous["project"]["path"] = None

    with pytest.raises(IdentityError) as excinfo:
        ensure_same_project(anonymous, report_document())
    assert "no project path" in str(excinfo.value)


def test_cli_refuses_two_different_projects(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("REPROCHECK_STATE_DIR", str(tmp_path / "state"))
    baseline = _baseline_file(tmp_path, report_document())
    other = report_document()
    other["project"]["path"] = str(tmp_path / "elsewhere")
    current = _write(tmp_path / "other.json", other)

    code = main(["baseline", "compare", str(baseline), str(current)])

    assert code == EXIT_OPERATIONAL
    assert "refusing to compare two different projects" in capsys.readouterr().err


def test_project_identity_drives_the_state_directory(tmp_path, monkeypatch) -> None:
    """The comparison identity and the state directory cannot disagree."""
    monkeypatch.setenv("REPROCHECK_STATE_DIR", str(tmp_path / "state"))
    project = tmp_path / "the-project"
    project.mkdir()

    directory = project_output_dir(project)
    identity = project_identity(project)

    assert hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8] in directory.name
    assert project_output_dir(project) == directory
    assert project_output_dir(tmp_path / "other") != directory


# --------------------------------------------------------------------------- #
# 15. CLI
# --------------------------------------------------------------------------- #


def test_cli_save_then_compare(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("REPROCHECK_STATE_DIR", str(tmp_path / "state"))
    baseline = _write(tmp_path / "base.json", report_document())
    current = _write(
        tmp_path / "current.json",
        report_document(verdict="PARTIAL", findings=[finding()]),
    )

    assert main(["baseline", "save", str(baseline)]) == EXIT_OK
    saved = capsys.readouterr().out
    assert "ReproCheck baseline saved" in saved
    stored = Path(
        next(
            line.split(": ", 1)[1]
            for line in saved.splitlines()
            if line.strip().startswith("baseline:")
        )
    )

    assert main(["baseline", "compare", str(stored), str(current)]) == EXIT_OK
    output = capsys.readouterr().out
    assert "ReproCheck baseline comparison" in output
    assert "NOT_ATTEMPTED -> PARTIAL" in output
    assert "1 finding(s) added" in output
    diff_dir = stored.parent
    assert (diff_dir / DIFF_NAME).is_file()
    assert (diff_dir / "reprocheck-diff.md").is_file()


def test_cli_compare_reports_no_change(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("REPROCHECK_STATE_DIR", str(tmp_path / "state"))
    report = _write(tmp_path / "r.json", report_document())
    baseline = _baseline_file(tmp_path, report_document())

    assert main(["baseline", "compare", str(baseline), str(report)]) == EXIT_OK
    output = capsys.readouterr().out

    assert "No material reproducibility changes detected." in output
    assert "->" not in output


def test_cli_compare_exit_code_is_zero_even_with_changes(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("REPROCHECK_STATE_DIR", str(tmp_path / "state"))
    baseline = _baseline_file(tmp_path, report_document())
    current = _write(tmp_path / "c.json", report_document(verdict="PARTIAL"))

    assert main(["baseline", "compare", str(baseline), str(current)]) == EXIT_OK
    assert "Changes:" in capsys.readouterr().out


def test_cli_compare_missing_baseline_is_operational(tmp_path, capsys) -> None:
    current = _write(tmp_path / "c.json", report_document())

    code = main(["baseline", "compare", str(tmp_path / "nope.json"), str(current)])

    assert code == EXIT_OPERATIONAL
    assert "could not read" in capsys.readouterr().err


def test_cli_compare_invalid_baseline_is_operational(tmp_path, capsys) -> None:
    baseline = _write(tmp_path / "b.json", {"nope": True})
    current = _write(tmp_path / "c.json", report_document())

    code = main(["baseline", "compare", str(baseline), str(current)])

    assert code == EXIT_OPERATIONAL
    assert "not a ReproCheck report" in capsys.readouterr().err


def test_cli_save_refuses_to_overwrite(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("REPROCHECK_STATE_DIR", str(tmp_path / "state"))
    report = _write(tmp_path / "r.json", report_document())

    assert main(["baseline", "save", str(report)]) == EXIT_OK
    capsys.readouterr()
    assert main(["baseline", "save", str(report)]) == EXIT_OPERATIONAL
    assert "--force" in capsys.readouterr().err
    assert main(["baseline", "save", str(report), "--force"]) == EXIT_OK


def test_cli_baseline_save_rejects_a_non_report(tmp_path, capsys) -> None:
    path = _write(tmp_path / "x.json", {"hello": "world"})

    assert main(["baseline", "save", str(path)]) == EXIT_OPERATIONAL
    assert "not a ReproCheck report" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# 16. output hygiene
# --------------------------------------------------------------------------- #


def test_default_output_dir_is_outside_the_project(tmp_path, monkeypatch) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("REPROCHECK_STATE_DIR", str(state))
    project = tmp_path / "the-project"
    project.mkdir()

    directory = project_output_dir(project)

    assert project not in directory.parents
    assert directory.is_relative_to(state)
    assert directory.name.endswith("-" + directory.name.split("-")[-1])
    assert len(directory.name.split("-")[-1]) == 8


def test_two_projects_with_the_same_name_do_not_collide(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REPROCHECK_STATE_DIR", str(tmp_path / "state"))
    first = tmp_path / "a" / "demo"
    second = tmp_path / "b" / "demo"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    assert project_output_dir(first) != project_output_dir(second)


def test_state_dir_defaults_to_the_user_profile(monkeypatch) -> None:
    monkeypatch.delenv("REPROCHECK_STATE_DIR", raising=False)
    directory = state_dir()

    assert directory.name == "reprocheck"
    assert "reprocheck" in str(directory)


def test_scan_does_not_write_into_the_analysed_project(tmp_path, monkeypatch) -> None:
    from conftest import inhouse_backend_files

    monkeypatch.setenv("REPROCHECK_STATE_DIR", str(tmp_path / "state"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        inhouse_backend_files("rc-hygiene")["pyproject.toml"], encoding="utf-8"
    )
    before = sorted(item.name for item in project.iterdir())

    assert main(["scan", str(project)]) == EXIT_OK

    assert sorted(item.name for item in project.iterdir()) == before
    assert (tmp_path / "state").is_dir()
    written = list((tmp_path / "state").rglob(REPORT_NAME))
    assert len(written) == 1


def test_output_dir_overrides_the_state_directory(tmp_path, monkeypatch) -> None:
    from conftest import inhouse_backend_files

    monkeypatch.setenv("REPROCHECK_STATE_DIR", str(tmp_path / "state"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        inhouse_backend_files("rc-outdir")["pyproject.toml"], encoding="utf-8"
    )
    destination = tmp_path / "reports"

    code = main(
        [
            "scan",
            str(project),
            "--output-dir",
            str(destination),
            "--json",
            str(tmp_path / "explicit.json"),
        ]
    )

    assert code == EXIT_OK
    assert (destination / "reprocheck-report.md").is_file()
    assert (tmp_path / "explicit.json").is_file()
    assert not (tmp_path / "state").exists() or not list(
        (tmp_path / "state").rglob(REPORT_NAME)
    )


def test_baseline_default_destination_is_the_state_directory(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REPROCHECK_STATE_DIR", str(tmp_path / "state"))
    report = _write(tmp_path / "r.json", report_document())

    assert main(["baseline", "save", str(report)]) == EXIT_OK

    stored = list((tmp_path / "state").rglob(BASELINE_NAME))
    assert len(stored) == 1
