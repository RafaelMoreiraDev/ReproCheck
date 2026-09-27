"""Tests for the ``reprocheck scan`` command line interface."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from reprocheck.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main

PROJECT = {
    "pyproject.toml": "[project]\nname = 'example'\nrequires-python = '>=3.11'\n",
    "README.md": "# Example\n\n```bash\npip install -e .\n```\n",
    "a.py": "",
}


def test_scan_writes_default_report(make_project, tmp_path, monkeypatch) -> None:
    root = make_project(PROJECT)
    monkeypatch.chdir(tmp_path)

    assert main(["scan", str(root)]) == EXIT_OK

    report = tmp_path / "reprocheck-report.json"
    assert report.is_file()
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["project"]["name"] == "example"
    assert data["project"]["python_file_count"] == 1


def test_scan_custom_json_destination(make_project, tmp_path) -> None:
    root = make_project(PROJECT)
    destination = tmp_path / "nested" / "custom.json"

    assert main(["scan", str(root), "--json", str(destination)]) == EXIT_OK
    assert json.loads(destination.read_text(encoding="utf-8"))["project"]["path"]


def test_verbose_output_adds_details(make_project, tmp_path, capsys) -> None:
    root = make_project(PROJECT)
    destination = tmp_path / "report.json"

    main(["scan", str(root), "--json", str(destination), "--verbose"])
    output = capsys.readouterr().out

    assert "README commands" in output
    assert "pip install -e ." in output
    assert "Package managers" in output


def test_default_output_is_plain(make_project, tmp_path, capsys) -> None:
    root = make_project(PROJECT)
    main(["scan", str(root), "--json", str(tmp_path / "r.json")])
    output = capsys.readouterr().out

    assert output.startswith("ReproCheck")
    assert "README commands" not in output
    assert "Report:" in output


def test_missing_path_returns_error(tmp_path, capsys) -> None:
    code = main(["scan", str(tmp_path / "nope"), "--json", str(tmp_path / "r.json")])
    assert code == EXIT_ERROR
    assert "error" in capsys.readouterr().err


def test_no_subcommand_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == EXIT_USAGE


def test_module_entry_point(make_project, tmp_path) -> None:
    root = make_project(PROJECT)
    destination = tmp_path / "module.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "reprocheck",
            "scan",
            str(root),
            "--json",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == EXIT_OK, completed.stderr
    assert destination.is_file()


def test_console_script_help() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "reprocheck", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == EXIT_OK
    assert "scan" in completed.stdout


CONFLICTING = {
    "pyproject.toml": "[project]\nname = 'x'\nrequires-python = '>=3.11'\n",
    ".python-version": "3.10\n",
    "a.py": 'DATA = "/home/someone/data.csv"\n',
    "README.md": "# x\n\n```bash\npip install -r missing.txt\n```\n",
}


def test_findings_are_grouped_by_severity(make_project, tmp_path, capsys) -> None:
    root = make_project(CONFLICTING)
    main(["scan", str(root), "--json", str(tmp_path / "r.json")])
    output = capsys.readouterr().out

    assert "Findings\n  0 errors\n" in output
    assert "warnings" in output
    assert "info" in output
    assert "  WARN RC101 " in output
    assert "  WARN RC120 " in output
    assert "  WARN RC130 " in output


def test_relative_path_argument(make_project, tmp_path, monkeypatch) -> None:
    make_project(PROJECT, name="relative-sample")
    monkeypatch.chdir(tmp_path)
    destination = tmp_path / "rel.json"

    assert main(["scan", "relative-sample", "--json", str(destination)]) == EXIT_OK
    data = json.loads(destination.read_text(encoding="utf-8"))
    assert data["project"]["name"] == "relative-sample"
    assert Path(data["project"]["path"]).is_absolute()
