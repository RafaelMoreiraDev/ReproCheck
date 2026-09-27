# ReproCheck

ReproCheck is a **read-only scanner for reproducibility** of Python projects.

Given a project directory, it answers a single question with evidence:

> If someone receives this repository today, is it possible to reconstruct the
> exact environment that produced its results — and does the project itself say
> how?

Most "it works on my machine" problems are not code bugs. They are missing or
contradictory facts: no pinned Python version, two competing package managers,
a documented install command that no longer exists, an uncommitted working tree
at the moment results were produced. ReproCheck collects those facts into a
single deterministic JSON report that can be diffed, archived and compared over
time.

## V0.1 is strictly read-only

This is a hard guarantee, not a best effort. When scanning a project, ReproCheck
**never**:

- modifies, creates or deletes any file inside the analysed project;
- installs dependencies or creates a `.venv`;
- executes the project's Python, its tests, or anything found in its README;
- imports any module from the analysed project;
- downloads files or performs any network request;
- changes Git state (no `checkout`, `reset`, `clean`, `commit`, no hooks).

The only write ReproCheck performs is the JSON report, at the exact path you
choose (by default `./reprocheck-report.json`, in your current directory).

Git is inspected with read-only commands only (`rev-parse`, `status
--porcelain`), executed with `--no-optional-locks` so that even the Git index is
left untouched.

## Install for development

Requires Python 3.11 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## Usage

```powershell
reprocheck scan C:\Projetos\some-project
```

or, without installing the console script:

```powershell
python -m reprocheck scan C:\Projetos\some-project
```

Options:

| Option | Description |
| --- | --- |
| `--json <arquivo>` | Report destination. Default: `./reprocheck-report.json` |
| `--verbose` | Also print HEAD, package manager hints and README commands |

Exit codes: `0` success, `1` report could not be written, `2` usage error or
unreadable path.

### Example

```text
ReproCheck

Project: example
Path: C:\Projetos\example
Python files: 3

Git
  repository: yes
  branch: main
  head: 9f1c2b7...
  clean: yes

Python
  .python-version: 3.11
  pyproject.toml [project.requires-python]: >=3.11
  .github/workflows/ci.yml [matrix.python-version]: 3.11

Detected
  [OK] pyproject.toml  (Python config)
  [OK] README.md  (README)
  [OK] tests/  (tests/)
  [OK] .github/workflows/ci.yml  (GitHub Actions)

Findings
  INFO RC009 Signals for more than one package manager were found: setuptools, uv. ...

Report:
  C:\Projetos\ReproCheck\reprocheck-report.json
```

### Report

```json
{
  "reprocheck_version": "0.1.0",
  "scan_timestamp": "2026-01-01T12:00:00+00:00",
  "project": { "name": "example", "path": "C:\\Projetos\\example", "python_file_count": 3 },
  "git": { "is_repository": true, "branch": "main", "head": "9f1c2b7...", "is_clean": true },
  "detected_files": [{ "path": "pyproject.toml", "kind": "file", "category": "python-config" }],
  "python_requirements": [{ "source": ".python-version", "value": "3.11", "file": ".python-version", "line": 1 }],
  "package_manager_hints": [{ "manager": "uv", "evidence": "uv.lock" }],
  "readme_commands": [{ "command": "pip install -e .", "file": "README.md", "line": 12 }],
  "findings": []
}
```

Conflicting Python versions are **never reconciled** in V0.1: every declaration
is recorded separately, per source. Deciding which one wins is out of scope for
this version.

## Findings in V0.1

| ID | Severity | Meaning |
| --- | --- | --- |
| RC001 | warning | No README detected |
| RC002 | warning | No Python version requirement detected |
| RC003 | warning | No Python dependency/configuration file detected |
| RC004 | warning | Directory is not a Git repository |
| RC005 | info | Working tree has uncommitted changes |
| RC006 | info | No `tests/` directory detected |
| RC007 | info | No GitHub Actions workflows detected |
| RC008 | info | No `.gitignore` detected |
| RC009 | info | Signals for more than one package manager |
| RC010 | info | No `.py` file detected |

Every finding states a verifiable fact. V0.1 does not attempt to diagnose
root causes or judge code quality.

## Development

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

## Current limitations

- Python projects only.
- No analysis of source code, imports or dependency resolution.
- Workflow parsing is line-based; YAML anchors, multi-line values and
  `env`-based indirection are not resolved.
- README extraction is textual; it records commands, it does not interpret
  them, and it may miss commands written outside code blocks.
- Findings are objective observations only — no reproducibility verdict, no
  scoring, no ranking of problems.
- No AI, no network, no sandbox, no auto-fix.

## Roadmap

- `0.2` — reproducibility verdict: reconcile Python requirements and package
  managers into a small set of objective conflicts (P1–P13 style problems).
- `0.3` — baseline reports: store a report and diff it against a new scan to
  show reproducibility drift over time.
- `0.4` — non-Python ecosystems (Node) and richer documentation parsing.
