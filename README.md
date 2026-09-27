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

## How it is organised

ReproCheck separates three layers, and never mixes them:

| Layer | Question | Module |
| --- | --- | --- |
| **FACT** | What was observed, and where? | `reprocheck.facts`, `reprocheck.scanners` |
| **CHECK** | Which deterministic rule does that fact break? | `reprocheck.checks` |
| **FINDING** | What can be stated, with which evidence and confidence? | `reprocheck.models.Finding` |

A check is a pure function of the facts. It cannot read the filesystem, run code
or guess: if a conclusion cannot be proven, no finding is emitted.

## V0.1 and V0.2 are strictly read-only

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
  0 errors
  2 warnings
  1 info

  WARN RC116 pyproject.toml [tool.uv] section configures uv, but uv.lock is not present; ...
  WARN RC140 The root .gitignore does not cover .venv/, .pytest_cache/, ... 
  INFO RC100 3 Python version declaration(s) were found and no inconsistency could be proven.

Report:
  C:\Projetos\ReproCheck\reprocheck-report.json
```

### Report

```json
{
  "reprocheck_version": "0.2.0",
  "report_schema_version": "1",
  "scan_timestamp": "2026-01-01T12:00:00+00:00",
  "project": { "name": "example", "path": "C:\\Projetos\\example", "python_file_count": 3 },
  "git": { "is_repository": true, "branch": "main", "head": "9f1c2b7...", "is_clean": true },
  "detected_files": [{ "path": "pyproject.toml", "kind": "file", "category": "python-config" }],
  "python_requirements": [{ "source": ".python-version", "value": "3.11", "file": ".python-version", "line": 1 }],
  "package_manager_hints": [{ "manager": "uv", "evidence": "uv.lock" }],
  "readme_commands": [{ "command": "pip install -e .", "file": "README.md", "line": 12 }],
  "findings": [
    {
      "id": "RC101",
      "title": "Local Python version conflicts with project requirement",
      "severity": "warning",
      "category": "python",
      "message": ".python-version selects Python 3.10, which does not satisfy '>=3.11' declared by pyproject.toml.",
      "evidence": "3.10 vs >=3.11",
      "file": ".python-version",
      "line": 1,
      "confidence": "high"
    }
  ],
  "facts": { "package_manager_signals": [], "readme_references": [], "absolute_paths": [], "file_references": [], "tools": [], "gitignore": {}, "project_metadata": {} }
}
```

`report_schema_version` is `"1"`. The V0.1 keys are unchanged; V0.2 only adds
`report_schema_version`, `facts` and `confidence` inside each finding.

Every Python version declaration is always recorded separately, per source.
Which one wins is never decided: the checks only state whether the declarations
can be satisfied together.

## Findings

### Basic facts (V0.1)

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
| RC009 | info | Signals for more than one active dependency-management tool |
| RC010 | info | No `.py` file detected |

### Consistency rules (V0.2)

| ID | Severity | Meaning |
| --- | --- | --- |
| RC100 | info | Python declarations exist and none of the rules below fired |
| RC101 | warning | `.python-version` does not satisfy the project requirement |
| RC102 | warning | CI runs a Python version the project does not support |
| RC103 | error | No candidate version satisfies every declared constraint |
| RC104 | info | A Python declaration is not valid PEP 440 and was not reconciled |
| RC110 | warning | Lockfiles from more than one tool are present |
| RC111 | warning | A lockfile exists with no configuration or documented use of its tool |
| RC112 | warning | The documented package manager is neither configured nor locked |
| RC113 | warning | A lockfile exists without its declaration file (`Pipfile.lock` without `Pipfile`) |
| RC114 | info | The documented install cannot consume the project lockfile |
| RC115 | warning | The documented install of a package is absent from the project metadata |
| RC116 | warning | A configured dependency tool ships no lockfile |
| RC120 | warning | Hard-coded absolute local path (`C:\...`, `/home/...`, `/Users/...`) |
| RC121 | warning | A literal path passed to a file-reading call was not found in the repository |
| RC130 | warning | The README documents a requirements file that does not exist |
| RC131 | warning | The README documents a script or test path that does not exist |
| RC132 | warning | The README documents a directory that does not exist |
| RC140 | warning | Generated artifacts of detected tools are not ignored by `.gitignore` |

### Confidence

Every finding carries `confidence`, derived from objective criteria only:

- `high` — both sides of the statement were read directly from the repository
  (for example a `.python-version` value and a `requires-python` value).
- `medium` — the statement is true but admits an innocent explanation, such as
  a path created or downloaded at runtime, or a stale-looking lockfile.
- `low` — not used in V0.2.

Severities never imply a bug verdict. A `warning` means "this deserves a human
look", not "this is broken".

## Development

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

The only runtime dependency is [`packaging`](https://pypi.org/project/packaging/),
used for PEP 440 parsing and version comparison. Implementing a partial
specifier parser was judged more dangerous than depending on the reference
implementation.

## Benchmark

`docs/benchmark-openclimatefix.md` records how V0.2 performs against a real
external repository, including the problems it does **not** detect.

## Current limitations

- Python projects only.
- Workflow parsing is line-based; YAML anchors, multi-line values and
  `env`-based indirection are not resolved.
- README extraction is textual; it records commands, it does not interpret
  them, and it may miss commands written outside code blocks.
- `.gitignore` comparison is an exact match on the normalised pattern; glob
  semantics (`**/build/`, `*.log`) are not evaluated, and nested `.gitignore`
  files are ignored.
- Path scanning is regex-based over `.py`, `.toml`, `.yaml`, `.yml`, `.json`,
  `.ini` and `.cfg` files; references built at runtime are not resolved.
- No dependency analysis: declared versions are not compared with each other or
  with a registry.
- No CI reference analysis: `uses: ...@main` is not flagged.
- Findings are objective observations only — no reproducibility verdict, no
  scoring, no ranking of problems.
- No AI, no network, no sandbox, no auto-fix.

## Roadmap

- `0.3` — baseline reports: store a report and diff it against a new scan to
  show reproducibility drift over time.
- `0.4` — dependency declaration analysis (pinning consistency, lockfile
  coverage) and CI reference pinning.
- `0.5` — non-Python ecosystems (Node) and richer documentation parsing.
