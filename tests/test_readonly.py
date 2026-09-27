"""Guarantees that scanning never modifies the analysed project."""

from __future__ import annotations

import json
import os
from pathlib import Path

from conftest import requires_git, run_git
from reprocheck.cli import main
from reprocheck.scanner import scan

PROJECT = {
    "pyproject.toml": "[project]\nname = 'example'\nrequires-python = '>=3.11'\n",
    ".python-version": "3.11.4\n",
    "requirements.txt": "requests\n",
    "uv.lock": "",
    "README.md": "# Example\n\n```bash\npip install -e .\npytest\n```\n",
    ".gitignore": "__pycache__/\n",
    "src/example/__init__.py": "",
    "src/example/main.py": "print('hi')\n",
    "tests/test_main.py": "def test_ok():\n    assert True\n",
    ".github/workflows/ci.yml": "name: ci\n",
}


def fingerprint(root: Path) -> dict[str, tuple[int, int]]:
    """Map every path under ``root`` to ``(size, mtime_ns)``."""
    result: dict[str, tuple[int, int]] = {}
    for current, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(dirnames):
            path = Path(current) / name
            stat = path.stat()
            result[path.relative_to(root).as_posix() + "/"] = (0, stat.st_mtime_ns)
        for name in sorted(filenames):
            path = Path(current) / name
            stat = path.stat()
            result[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return result


def test_scan_does_not_touch_the_project(make_project, tmp_path) -> None:
    root = make_project(PROJECT)
    before = fingerprint(root)

    scan(root)

    assert fingerprint(root) == before


@requires_git
def test_scan_of_git_project_does_not_touch_anything(git_project, tmp_path) -> None:
    root = git_project(PROJECT)
    head_before = run_git(root, "rev-parse", "HEAD").stdout
    status_before = run_git(root, "status", "--porcelain").stdout
    before = fingerprint(root)

    scan(root)

    assert fingerprint(root) == before
    assert run_git(root, "rev-parse", "HEAD").stdout == head_before
    assert run_git(root, "status", "--porcelain").stdout == status_before


@requires_git
def test_scan_does_not_change_the_current_commit(git_project) -> None:
    root = git_project(PROJECT)
    head = run_git(root, "rev-parse", "HEAD").stdout.strip()
    branch = run_git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    scan(root)

    assert run_git(root, "rev-parse", "HEAD").stdout.strip() == head
    assert run_git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == branch


def test_cli_writes_report_outside_the_project(make_project, tmp_path) -> None:
    root = make_project(PROJECT)
    before = fingerprint(root)
    destination = tmp_path / "reports" / "report.json"

    assert main(["scan", str(root), "--json", str(destination)]) == 0

    assert fingerprint(root) == before
    assert json.loads(destination.read_text(encoding="utf-8"))["project"]["name"]
    for forbidden in (".venv", "venv", "__pycache__", ".pytest_cache"):
        assert not (root / forbidden).exists()


def test_readme_commands_are_not_executed(make_project, tmp_path) -> None:
    canary = tmp_path / "canary.txt"
    project = dict(PROJECT)
    project["README.md"] = (
        f"# Example\n\n```bash\npython -c \"open(r'{canary}', 'w').write('x')\"\n```\n"
    )
    root = make_project(project)

    report = scan(root)

    assert any("canary" in item.command for item in report.readme_commands)
    assert not canary.exists()


def test_dependency_audit_does_not_touch_the_project(make_project) -> None:
    """Scanning dependency declarations must not modify anything."""
    root = make_project(
        {
            "pyproject.toml": (
                "[project]\n"
                "name = 'x'\n"
                "dependencies = ['numpy==1.23.5', 'pandas==2.0.0', 'requests']\n"
                "[dependency-groups]\n"
                "dev = ['numpy==2.0.0']\n"
                "[build-system]\n"
                "requires = ['setuptools>=67']\n"
            ),
            "requirements.txt": "-r missing/base.txt\n-c also-missing.txt\n",
            "requirements-dev.txt": "numpy>=2\n",
            "a.py": "",
        }
    )
    before = fingerprint(root)

    report = scan(root)
    ids = {finding.id for finding in report.findings}

    assert {"RC200", "RC203", "RC204", "RC206", "RC207"} <= ids
    assert fingerprint(root) == before


def test_consistency_checks_do_not_touch_the_project(make_project) -> None:
    """A project that triggers every new check must stay byte-identical."""
    root = make_project(
        {
            "pyproject.toml": (
                "[project]\nname = 'x'\nrequires-python = '>=3.11'\n"
                "[tool.uv]\ndev-dependencies = []\n"
            ),
            ".python-version": "3.10\n",
            "uv.lock": "",
            "poetry.lock": "",
            "Pipfile.lock": "{}",
            ".gitignore": "__pycache__/\n",
            "README.md": (
                "# x\n\n```bash\n"
                "pip install -r requirements.txt\n"
                "python scripts/train.py\n"
                "conda install pyresample\n"
                "```\n"
            ),
            "a.py": 'DATA = "/home/someone/data.csv"\n',
            "analysis/run.py": 'frame = read_csv("dataset/testset.csv")\n',
            "tests/test_a.py": "",
        }
    )
    before = fingerprint(root)

    report = scan(root)
    ids = {finding.id for finding in report.findings}

    assert {
        "RC101",
        "RC110",
        "RC111",
        "RC113",
        "RC115",
        "RC120",
        "RC121",
        "RC130",
        "RC131",
        "RC140",
    } <= ids
    assert fingerprint(root) == before
