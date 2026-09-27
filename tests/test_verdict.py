"""Tests for the verdict model, the Markdown report and the CLI policy.

The policy tests build reports synthetically, so every verdict rule is checked
without a subprocess. The end-to-end tests run a real reproduction in the
isolated workspace with local wheels only: nothing here reaches the network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import inhouse_backend_files, make_wheel
from reprocheck.checks.reproduction import check_reproduction
from reprocheck.checks.verdict import compute_verdict
from reprocheck.cli import (
    EXIT_FAIL,
    EXIT_OK,
    EXIT_OPERATIONAL,
    EXIT_PARTIAL,
    main,
)
from reprocheck.models import (
    Confidence,
    Finding,
    ProjectScan,
    ScanReport,
    Severity,
    VerdictStatus,
)
from reprocheck.reporters import render_markdown, write_markdown
from reprocheck.reproduction.installer import STRATEGY_PROJECT
from reprocheck.reproduction.models import (
    FingerprintResult,
    ImportCheck,
    InstallationAttempt,
    InstalledDistribution,
    PipCheckResult,
    PythonSelection,
    ReproductionReport,
    VenvInfo,
)
from reprocheck.reproduction.models import TestCollection as PytestCollectionResult
from reprocheck.reproduction.runner import reproduce
from reprocheck.scanner import scan

HOST_PYTHON_MINOR = f"{sys.version_info[0]}.{sys.version_info[1]}"

#: Sentinel so ``installation=None`` can mean "no installation was performed".
_DEFAULT = object()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _finding(
    ident: str = "RC203",
    severity: Severity = Severity.WARNING,
    **kwargs,
) -> Finding:
    payload = {
        "id": ident,
        "title": "Unbounded runtime dependency",
        "severity": severity,
        "category": "dependencies",
        "message": "'requests' is declared without a version bound.",
        "evidence": "pyproject.toml:12",
        "file": "pyproject.toml",
        "line": 12,
        "confidence": Confidence.HIGH,
    }
    payload.update(kwargs)
    return Finding(**payload)  # type: ignore[arg-type]


def _collection(
    *,
    available: bool = True,
    ran: bool = True,
    success: bool = True,
    collected: int = 3,
) -> dict:
    if not available:
        return {
            "available": False,
            "ran": False,
            "success": None,
            "exit_code": None,
            "collected": None,
            "reason": "pytest is not installed in the reproduced environment",
        }
    return {
        "available": True,
        "ran": ran,
        "success": success,
        "exit_code": 0 if success else 2,
        "collected": collected if ran else None,
        "reason": None,
    }


def _reproduction(
    *,
    runtime_checks: bool = True,
    imports: list[dict] | None = None,
    collection: dict | None = None,
    install_success: bool = True,
    pip_clean: bool = True,
    unchanged: bool = True,
    installation: dict | object = _DEFAULT,
    completed_steps: list[str] | None = None,
) -> dict:
    """A reproduction report shaped exactly like ``ReproductionReport.to_dict``."""
    if imports is None:
        imports = [
            {"module": "rc_demo", "imported": True, "exit_code": 0, "timed_out": False}
        ]
    if collection is None:
        collection = _collection(available=False)
    if installation is _DEFAULT:
        installation = {
            "success": install_success,
            "strategy": "pip-install-source",
            "exit_code": 0 if install_success else 1,
            "duration_seconds": 12.5,
            "stderr_path": "C:/tmp/rc/logs/install.stderr.log",
        }
    return {
        "attempted": True,
        "workspace": None,
        "workspace_kept": False,
        "source": "C:/tmp/rc/source",
        "original_project": "C:/tmp/rc",
        "network_enabled": False,
        "runtime_checks_enabled": runtime_checks,
        "python": {
            "selected": "3.11",
            "reason": "declared requirement",
            "executable": "python",
        },
        "venv": {
            "created": True,
            "python_version": "3.11.16",
            "path": "C:/tmp/rc/venv",
        },
        "installation": installation,
        "pip_check": {
            "ran": True,
            "clean": pip_clean,
            "exit_code": 0 if pip_clean else 1,
            "conflict_count": 0 if pip_clean else 1,
            "conflicts": []
            if pip_clean
            else ["alpha requires sharedlib==1.0, but 2.0 is installed"],
            "stdout_path": "C:/tmp/rc/logs/pip-check.stdout.log",
        },
        "installed_distribution": {
            "name": "rc-demo",
            "version": "0.1.0",
            "looks_like_fallback": False,
        },
        "runtime_checks": {
            "enabled": runtime_checks,
            "import_discovery": "distribution 'rc-demo' provides 1 top-level module",
            "imports": imports,
            "pytest_collection": collection,
        },
        "original_project_unchanged": unchanged,
        "integrity": {
            "unchanged": unchanged,
            "changed_paths": [] if unchanged else ["data/out.csv"],
        },
        "completed_steps": completed_steps
        or [
            "static scan",
            "workspace created",
            "project copied",
            "python selected: 3.11",
        ],
    }


def _report(
    *,
    findings: list[Finding] | None = None,
    reproduction_findings: list[Finding] | None = None,
    reproduction: dict | None = None,
    python_files: int = 4,
) -> ScanReport:
    report = ScanReport(
        reprocheck_version="0.7.0",
        scan_timestamp="2026-01-01T00:00:00+00:00",
        project=ProjectScan(
            name="demo", path="C:/tmp/rc", python_file_count=python_files
        ),
        report_schema_version="6",
    )
    report.findings = list(findings or [])
    report.reproduction_findings = list(reproduction_findings or [])
    if reproduction is not None:
        report.reproduction = reproduction
    report.verdict = compute_verdict(report)
    return report


def _markdown(**kwargs) -> str:
    return render_markdown(_report(**kwargs))


# --------------------------------------------------------------------------- #
# 1. scan without reproduce
# --------------------------------------------------------------------------- #


def test_scan_is_not_attempted(make_project) -> None:
    report = scan(make_project(inhouse_backend_files("rc-scan")))
    data = report.to_dict()

    assert report.verdict.status is VerdictStatus.NOT_ATTEMPTED
    assert data["verdict"]["status"] == "NOT_ATTEMPTED"  # type: ignore[index]
    assert "reproduction" not in data
    assert "no reproduction was attempted" in report.verdict.reasons[0]


def test_scan_json_has_the_verdict_block(make_project, tmp_path) -> None:
    destination = tmp_path / "report.json"
    main(
        [
            "scan",
            str(make_project(inhouse_backend_files("rc-json"))),
            "--json",
            str(destination),
            "--markdown",
            str(tmp_path / "report.md"),
        ]
    )
    data = json.loads(destination.read_text(encoding="utf-8"))

    assert set(data["verdict"]) == {"status", "reasons", "not_verified"}
    assert data["verdict"]["status"] == "NOT_ATTEMPTED"
    assert data["verdict"]["not_verified"]


# --------------------------------------------------------------------------- #
# 2. verdict rules
# --------------------------------------------------------------------------- #


def test_complete_successful_reproduction_is_pass() -> None:
    report = _report(
        reproduction=_reproduction(runtime_checks=True),
        reproduction_findings=[],
    )

    assert report.verdict.status is VerdictStatus.PASS
    assert report.verdict.reasons == ()


def test_pass_is_reachable_with_info_findings_only() -> None:
    report = _report(
        findings=[_finding("RC006", Severity.INFO), _finding("RC007", Severity.INFO)],
        reproduction=_reproduction(runtime_checks=True),
    )

    assert report.verdict.status is VerdictStatus.PASS


def test_runtime_checks_not_requested_is_partial() -> None:
    report = _report(reproduction=_reproduction(runtime_checks=False))

    assert report.verdict.status is VerdictStatus.PARTIAL
    assert any(
        "runtime checks were not requested" in reason
        for reason in report.verdict.reasons
    )


def test_open_warning_is_partial_not_fail() -> None:
    report = _report(
        findings=[_finding("RC203"), _finding("RC220", Severity.WARNING)],
        reproduction=_reproduction(runtime_checks=True),
    )

    assert report.verdict.status is VerdictStatus.PARTIAL
    assert any(
        "RC203" in reason and "RC220" in reason for reason in report.verdict.reasons
    )


def test_static_findings_alone_never_fail() -> None:
    report = _report(
        findings=[_finding("RC150"), _finding("RC120"), _finding("RC116")],
        reproduction=_reproduction(runtime_checks=True),
    )

    assert report.verdict.status is VerdictStatus.PARTIAL
    assert not report.verdict.failed


def test_failed_installation_is_fail() -> None:
    report = _report(
        reproduction=_reproduction(
            install_success=False,
            runtime_checks=False,
            completed_steps=["static scan", "installation failed"],
            installation=None,
        ),
        reproduction_findings=[
            _finding("RC401", Severity.ERROR, category="reproduction")
        ],
    )

    assert report.verdict.status is VerdictStatus.FAIL
    assert any(
        "installation step failed" in reason for reason in report.verdict.reasons
    )


def test_pip_check_conflict_is_fail() -> None:
    report = _report(
        reproduction=_reproduction(pip_clean=False),
        reproduction_findings=[
            _finding("RC400", Severity.ERROR, category="reproduction")
        ],
    )

    assert report.verdict.status is VerdictStatus.FAIL
    assert any("RC400" in reason for reason in report.verdict.reasons)


def test_failed_import_is_fail() -> None:
    report = _report(
        reproduction=_reproduction(
            imports=[
                {
                    "module": "rc_demo",
                    "imported": False,
                    "exit_code": 1,
                    "stderr_snippet": "boom",
                }
            ]
        ),
        reproduction_findings=[
            _finding("RC500", Severity.ERROR, category="reproduction")
        ],
    )

    assert report.verdict.status is VerdictStatus.FAIL


def test_import_failure_without_runtime_checks_is_not_a_failure() -> None:
    """No import was attempted, so nothing about imports can fail."""
    report = _report(
        reproduction=_reproduction(runtime_checks=False),
        reproduction_findings=[],
    )

    assert report.verdict.status is VerdictStatus.PARTIAL
    assert not report.verdict.failed


def test_collection_failure_is_partial_not_fail() -> None:
    report = _report(
        reproduction=_reproduction(
            collection=_collection(available=True, ran=True, success=False)
        ),
        reproduction_findings=[
            _finding("RC501", Severity.WARNING, category="reproduction")
        ],
    )

    assert report.verdict.status is VerdictStatus.PARTIAL
    assert not report.verdict.failed


def test_indeterminable_import_target_is_partial() -> None:
    reproduction = _reproduction(imports=[])
    reproduction["runtime_checks"]["import_discovery"] = (
        "no unambiguous top-level module"
    )
    report = _report(reproduction=reproduction)

    assert report.verdict.status is VerdictStatus.PARTIAL
    assert any("no importable module" in reason for reason in report.verdict.reasons)


def test_missing_installation_is_partial_when_nothing_failed() -> None:
    report = _report(
        reproduction=_reproduction(
            installation=None,
            runtime_checks=False,
            completed_steps=["static scan", "no installation strategy"],
        ),
        reproduction_findings=[
            _finding("RC403", Severity.WARNING, category="reproduction")
        ],
    )

    assert report.verdict.status is VerdictStatus.PARTIAL
    assert any(
        "no installation was performed" in reason for reason in report.verdict.reasons
    )


def test_failed_pip_check_from_a_real_run_is_fail() -> None:
    """The RC400 finding is produced from a real pip check result."""
    reproduction = ReproductionReport(
        installation=InstallationAttempt(
            strategy=STRATEGY_PROJECT,
            command=("python", "-m", "pip", "install", "."),
            cwd="C:/tmp/rc/source",
            source="C:/tmp/rc/source",
            venv="C:/tmp/rc/venv",
            network_enabled=False,
            exit_code=0,
            success=True,
        ),
        pip_check=PipCheckResult(
            ran=True,
            clean=False,
            exit_code=1,
            conflict_count=1,
            conflicts=("alpha requires sharedlib==1.0, but 2.0 is installed",),
        ),
    )
    findings = check_reproduction(reproduction)
    report = _report(
        reproduction=reproduction.to_dict(), reproduction_findings=findings
    )

    assert "RC400" in {item.id for item in findings}
    assert report.verdict.status is VerdictStatus.FAIL


# --------------------------------------------------------------------------- #
# 3. not verified versus failure
# --------------------------------------------------------------------------- #


def test_pytest_absent_is_not_verified_and_does_not_block_pass() -> None:
    report = _report(
        reproduction=_reproduction(
            runtime_checks=True, collection=_collection(available=False)
        )
    )

    assert report.verdict.status is VerdictStatus.PASS
    items = {item.item for item in report.verdict.not_verified}
    assert "test collection" in items
    assert "full test suite" in items
    assert not any("collection" in reason for reason in report.verdict.reasons)


def test_not_verified_is_separate_from_failures() -> None:
    failed = _report(
        reproduction=_reproduction(
            install_success=False, runtime_checks=False, installation=None
        ),
        reproduction_findings=[
            _finding("RC401", Severity.ERROR, category="reproduction")
        ],
    )

    assert failed.verdict.status is VerdictStatus.FAIL
    # The unknowns are still listed, but only under ``not_verified``: none of
    # them is reported as a failure and none of them is a reason.
    assert {item.item for item in failed.verdict.not_verified} >= {
        "full test suite",
        "external datasets and model downloads",
    }
    assert all(
        isinstance(item.item, str) and isinstance(item.reason, str)
        for item in failed.verdict.not_verified
    )
    assert not any("not verified" in reason for reason in failed.verdict.reasons)


def test_collected_tests_are_listed_as_not_verified() -> None:
    report = _report(
        reproduction=_reproduction(runtime_checks=True, collection=_collection())
    )

    outcomes = [
        item for item in report.verdict.not_verified if item.item == "test outcomes"
    ]

    assert report.verdict.status is VerdictStatus.PASS
    assert outcomes and "no test was executed" in outcomes[0].reason


# --------------------------------------------------------------------------- #
# 4. Markdown report
# --------------------------------------------------------------------------- #


def test_markdown_contains_evidence() -> None:
    markdown = _markdown(findings=[_finding("RC150")])

    assert "RC150 — WARNING — HIGH CONFIDENCE" in markdown
    assert "Unbounded runtime dependency." in markdown
    assert "'requests' is declared without a version bound." in markdown
    assert "Evidence:" in markdown
    assert "pyproject.toml:12" in markdown
    assert "Location: pyproject.toml:12" in markdown


def test_markdown_groups_by_severity_and_omits_empty_sections() -> None:
    markdown = _markdown(python_files=0)

    assert "## Problems found" in markdown
    assert "### Errors" not in markdown
    assert "### Warnings" not in markdown
    assert "## CI" not in markdown
    assert "## Dependencies" not in markdown
    assert "## Files and configuration" not in markdown
    assert "## Reproduction" not in markdown
    assert "## Recommended next actions" not in markdown
    assert "## What worked" not in markdown
    assert "## Not verified" in markdown
    assert "## Summary" in markdown
    assert "**NOT_ATTEMPTED**" in markdown


def test_markdown_reproduction_section_reports_each_step() -> None:
    markdown = _markdown(reproduction=_reproduction(runtime_checks=False))

    assert "## Reproduction" in markdown
    assert "- Installation: PASS (strategy: pip-install-source" in markdown
    assert "- pip check: PASS (no broken requirements)" in markdown
    assert "- Installed distribution: rc-demo 0.1.0" in markdown
    assert "- Original source tree: unchanged" in markdown


def test_markdown_what_worked_only_states_proven_facts() -> None:
    markdown = _markdown(
        reproduction=_reproduction(
            runtime_checks=True, collection=_collection(available=False)
        )
    )

    assert "## What worked" in markdown
    assert "Python 3.11 was selected successfully." in markdown
    assert "pip check reported no broken requirements." in markdown
    assert "1 top-level module(s) imported successfully: `rc_demo`." in markdown
    assert "The original source tree remained unchanged." in markdown
    # No positive claim is derived from the absence of findings.
    assert "No problems" not in markdown
    assert (
        "pytest"
        not in markdown.split("## Problems found")[0].split("## Not verified")[0]
    )


def test_markdown_not_verified_is_labelled_as_unknown() -> None:
    markdown = _markdown(reproduction=_reproduction(runtime_checks=True))

    assert "They are not failures: the result is unknown." in markdown
    assert "external datasets and model downloads: never accessed" in markdown


def test_markdown_is_deterministic() -> None:
    report = _report(findings=[_finding("RC150")], reproduction=_reproduction())

    assert render_markdown(report) == render_markdown(report)


def test_markdown_file_is_written_utf8(tmp_path) -> None:
    destination = tmp_path / "nested" / "report.md"
    written = write_markdown(_report(), destination)

    assert written == destination.resolve()
    text = destination.read_text(encoding="utf-8")
    assert text.startswith("# ReproCheck Report")
    assert text.endswith("\n")


# --------------------------------------------------------------------------- #
# 5. recommendations
# --------------------------------------------------------------------------- #


def test_recommendations_are_deterministic_and_unique_per_id() -> None:
    markdown = _markdown(
        findings=[
            _finding("RC150"),
            _finding("RC150", file="setup.py"),
            _finding("RC220"),
        ]
    )
    lines = [line for line in markdown.splitlines() if line.startswith("- **RC")]

    assert lines == sorted(lines)
    assert lines == [
        "- **RC150** — Reproduce with VCS metadata available if the exact package "
        "version matters, or record the released version explicitly.",
        "- **RC220** — Consider pinning the external GitHub Action/workflow to a "
        "full commit SHA if immutable CI inputs are required.",
    ]


def test_recommendation_wording_is_not_prescriptive() -> None:
    markdown = _markdown(findings=[_finding("RC203"), _finding("RC116")])

    assert "Decide whether the dependency should be bounded" in markdown
    assert "If exact versions matter, generate and commit a lockfile" in markdown
    assert "must" not in markdown.split("## Recommended next actions")[1]
    assert "ReproCheck changes nothing on its own." in markdown


def test_recommendation_for_rc401_points_at_the_log() -> None:
    markdown = _markdown(
        reproduction_findings=[
            _finding("RC401", Severity.ERROR, category="reproduction")
        ],
        reproduction=_reproduction(),
    )

    assert "Inspect the installation log before changing dependencies" in markdown


# --------------------------------------------------------------------------- #
# 6. end to end
# --------------------------------------------------------------------------- #


def _clean_project_files(name: str) -> dict[str, str]:
    """A project with no open warning, so the verdict rules are what is tested."""
    files = inhouse_backend_files(name)
    files.update(
        {
            "README.md": f"# {name}\n\n## Install\n\n```bash\npip install .\n```\n",
            ".gitignore": ("__pycache__/\n*.pyc\nbuild/\ndist/\n*.egg-info/\n.venv/\n"),
            "tests/__init__.py": "",
        }
    )
    return files


def test_reproduction_without_runtime_checks_is_partial(git_project, tmp_path) -> None:
    from reprocheck.reproduction import runner

    root = git_project(_clean_project_files("rc-e2e-quiet"))
    report = reproduce(root, base_dir=tmp_path / "workspaces")

    assert report.verdict.status is VerdictStatus.PARTIAL
    assert any("runtime checks were not requested" in r for r in report.verdict.reasons)
    assert {item.item for item in report.verdict.not_verified} >= {
        "module imports and test collection"
    }
    assert runner is not None


def test_reproduction_with_runtime_checks_reaches_pass(git_project, tmp_path) -> None:
    root = git_project(_clean_project_files("rc-e2e-pass"))
    report = reproduce(root, base_dir=tmp_path / "workspaces", runtime_checks=True)

    warnings = {
        item.id for item in report.findings if item.severity is Severity.WARNING
    }
    assert not warnings, f"the fixture must not produce warnings: {sorted(warnings)}"
    assert report.verdict.status is VerdictStatus.PASS
    markdown = render_markdown(report)
    assert "**PASS**" in markdown
    assert "- Runtime checks: enabled" in markdown


def test_broken_import_fails_end_to_end(make_project, tmp_path) -> None:
    files = inhouse_backend_files("rc-e2e-broken")
    files["rc_e2e_broken.py"] = "raise RuntimeError('boom')\n"
    root = make_project(files)

    report = reproduce(
        root, base_dir=tmp_path / "workspaces", runtime_checks=True, keep_workspace=True
    )

    assert report.verdict.status is VerdictStatus.FAIL
    assert report.reproduction["original_project_unchanged"] is True


def test_required_python_missing_fails(make_project, tmp_path) -> None:
    root = make_project(
        inhouse_backend_files("rc-e2e-python", requires_python="==3.99")
    )

    report = reproduce(root, base_dir=tmp_path / "workspaces")

    assert report.verdict.status is VerdictStatus.FAIL
    assert any("RC402" in reason for reason in report.verdict.reasons)


def test_project_without_distribution_name_is_partial(make_project, tmp_path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    make_wheel(wheelhouse, "plaindep", "1.0")
    root = tmp_path / "no-name"
    root.mkdir()
    (root / "requirements.txt").write_text(
        f'--no-index\n--find-links "{wheelhouse}"\nplaindep==1.0\n', encoding="utf-8"
    )
    (root / ".python-version").write_text(HOST_PYTHON_MINOR, encoding="utf-8")
    (root / "a.py").write_text("VALUE = 1\n", encoding="utf-8")

    report = reproduce(root, base_dir=tmp_path / "workspaces", runtime_checks=True)

    assert report.verdict.status is VerdictStatus.PARTIAL
    assert any("no importable module" in reason for reason in report.verdict.reasons)


def test_original_project_is_never_modified(make_project, tmp_path) -> None:
    root = make_project(_clean_project_files("rc-e2e-untouched"))
    before = sorted(path.name for path in root.iterdir())

    report = reproduce(root, base_dir=tmp_path / "workspaces", runtime_checks=True)

    assert sorted(path.name for path in root.iterdir()) == before
    assert report.reproduction["original_project_unchanged"] is True
    assert "RC406" not in {item.id for item in report.reproduction_findings}


# --------------------------------------------------------------------------- #
# 7. CLI
# --------------------------------------------------------------------------- #


def test_cli_scan_writes_both_reports(
    make_project, tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    root = make_project(inhouse_backend_files("rc-cli-scan"))

    code = main(["scan", str(root)])
    output = capsys.readouterr().out

    assert code == EXIT_OK
    assert (tmp_path / "reprocheck-report.json").is_file()
    assert (tmp_path / "reprocheck-report.md").is_file()
    assert "Verdict: NOT_ATTEMPTED" in output
    assert "reprocheck-report.md" in output
    assert "Not verified" in output


def test_cli_scan_custom_markdown_destination(make_project, tmp_path) -> None:
    root = make_project(inhouse_backend_files("rc-cli-md"))
    destination = tmp_path / "docs" / "custom.md"

    assert (
        main(
            [
                "scan",
                str(root),
                "--json",
                str(tmp_path / "r.json"),
                "--markdown",
                str(destination),
            ]
        )
        == EXIT_OK
    )
    assert destination.read_text(encoding="utf-8").startswith("# ReproCheck Report")


def test_cli_reproduce_exit_codes(make_project, tmp_path, monkeypatch, capsys) -> None:
    from reprocheck.reproduction import runner

    monkeypatch.setattr(runner, "DEFAULT_BASE_DIR", tmp_path / "cli-workspaces")
    partial = make_project(_clean_project_files("rc-cli-partial"), name="partial")
    failing = make_project(
        inhouse_backend_files("rc-cli-fail", requires_python="==3.99"), name="failing"
    )

    assert (
        main(
            [
                "reproduce",
                str(partial),
                "--json",
                str(tmp_path / "a.json"),
                "--markdown",
                str(tmp_path / "a.md"),
            ]
        )
        == EXIT_PARTIAL
    )
    assert "Verdict: PARTIAL" in capsys.readouterr().out
    assert (
        main(
            [
                "reproduce",
                str(failing),
                "--json",
                str(tmp_path / "b.json"),
                "--markdown",
                str(tmp_path / "b.md"),
            ]
        )
        == EXIT_FAIL
    )
    assert "Verdict: FAIL" in capsys.readouterr().out


def test_cli_runtime_checks_flag_reaches_the_reproduction(
    make_project, tmp_path, monkeypatch, capsys
) -> None:
    """The flag must be forwarded, not only accepted by the parser."""
    from reprocheck.reproduction import runner

    monkeypatch.setattr(runner, "DEFAULT_BASE_DIR", tmp_path / "cli-workspaces")
    root = make_project(_clean_project_files("rc-cli-runtime"), name="runtime")

    code = main(
        [
            "reproduce",
            str(root),
            "--runtime-checks",
            "--json",
            str(tmp_path / "r.json"),
            "--markdown",
            str(tmp_path / "r.md"),
        ]
    )
    output = capsys.readouterr().out
    data = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))

    assert data["reproduction"]["runtime_checks_enabled"] is True
    assert "runtime checks: enabled" in output
    # PARTIAL, not PASS: this fixture is not a Git repository, so RC004 is open.
    assert code == EXIT_PARTIAL
    assert "PASS (no broken requirements)" in (tmp_path / "r.md").read_text(
        encoding="utf-8"
    )


def test_cli_missing_path_is_an_operational_error(tmp_path, capsys) -> None:
    code = main(["scan", str(tmp_path / "nope"), "--json", str(tmp_path / "r.json")])

    assert code == EXIT_OPERATIONAL
    assert code not in {EXIT_OK, EXIT_PARTIAL, EXIT_FAIL}
    assert "error" in capsys.readouterr().err


def test_cli_help_documents_the_markdown_option(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["scan", "--help"])
    output = " ".join(capsys.readouterr().out.split())

    assert "--markdown" in output
    assert "Markdown report destination" in output


def test_markdown_default_name_is_the_documented_one() -> None:
    from reprocheck.reporters import DEFAULT_MARKDOWN_NAME, DEFAULT_REPORT_NAME

    assert DEFAULT_MARKDOWN_NAME == "reprocheck-report.md"
    assert DEFAULT_REPORT_NAME == "reprocheck-report.json"


def test_models_expose_the_verdict_types() -> None:
    from reprocheck.models.verdict import ReproducibilityVerdict

    assert [item.value for item in VerdictStatus] == [
        "PASS",
        "PARTIAL",
        "FAIL",
        "NOT_ATTEMPTED",
    ]
    assert ReproducibilityVerdict().to_dict() == {
        "status": "NOT_ATTEMPTED",
        "reasons": [],
        "not_verified": [],
    }


def test_import_check_and_selection_models_are_untouched() -> None:
    """The reproduction models used by the tests keep their shape."""
    assert InstalledDistribution(name="x", version="1.0").to_dict()["version"] == "1.0"
    assert ImportCheck(module="x", imported=True, exit_code=0).to_dict()["imported"]
    assert PytestCollectionResult(available=False).to_dict()["available"] is False
    assert PythonSelection(selected="3.11").to_dict()["selected"] == "3.11"
    assert VenvInfo(created=True).to_dict()["created"] is True
    assert FingerprintResult().to_dict()["unchanged"] is True
    assert Path("x").name == "x"
