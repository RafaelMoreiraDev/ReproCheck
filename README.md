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
deterministic JSON report, and projects the same content into a Markdown report
a person can read without knowing any check ID.

## How it is organised

ReproCheck separates four layers, and never mixes them:

| Layer | Question | Module |
| --- | --- | --- |
| **FACT** | What was observed, and where? | `reprocheck.facts`, `reprocheck.scanners` |
| **CHECK** | Which deterministic rule does that fact break? | `reprocheck.checks` |
| **FINDING** | What can be stated, with which evidence and confidence? | `reprocheck.models.Finding` |
| **VERDICT** | What do all of those facts add up to, and what is still unknown? | `reprocheck.models.verdict`, `reprocheck.checks.verdict` |

A check is a pure function of the facts. It cannot read the filesystem, run code
or guess: if a conclusion cannot be proven, no finding is emitted. The verdict is
a pure derivation of the findings: it adds no new claim, and it never produces a
numeric score.


## `scan` is strictly read-only

This is a hard guarantee, not a best effort. When **scanning** a project,
ReproCheck **never**:

- modifies, creates or deletes any file inside the analysed project;
- installs dependencies or creates a `.venv`;
- executes the project's Python, its tests, or anything found in its README;
- imports any module from the analysed project;
- downloads files or performs any network request;
- changes Git state (no `checkout`, `reset`, `clean`, `commit`, no hooks).

The only writes `scan` performs are the two reports, at the exact paths you
choose (by default `./reprocheck-report.json` and `./reprocheck-report.md`, in
your current directory). Nothing is written inside the analysed project.


Git is inspected with read-only commands only (`rev-parse`, `status
--porcelain`), executed with `--no-optional-locks` so that even the Git index is
left untouched.

`reproduce` is the single exception: it executes the project's build backend on
a **copy**, in a workspace outside the project, and the original is verified
before and after. See "Reproduce" below and `docs/reproduction-safety.md`.

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
reprocheck reproduce C:\Projetos\some-project
```

or, without installing the console script:

```powershell
python -m reprocheck scan C:\Projetos\some-project
python -m reprocheck reproduce C:\Projetos\some-project
```

`scan` options:

| Option | Description |
| --- | --- |
| `--json <arquivo>` | JSON report destination. Default: `./reprocheck-report.json` |
| `--markdown <arquivo>` | Markdown report destination. Default: `./reprocheck-report.md` |
| `--verbose` | Also print HEAD, package manager hints and README commands |

`reproduce` takes the same two report options, plus `--network`,
`--keep-workspace`, `--runtime-checks` and `--verbose`.

### Exit codes

| Code | `scan` | `reproduce` |
| --- | --- | --- |
| `0` | report written | verdict is `PASS` |
| `1` | — | verdict is `PARTIAL` |
| `2` | — | verdict is `FAIL` |
| `3` | path unusable, or a report could not be written | same |

`2` is also what argparse uses for a malformed command line, which is why
operational errors use `3`.

### Example

```text
ReproCheck

Verdict: NOT_ATTEMPTED
  - no reproduction was attempted; only static facts were collected

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

Dependencies
  runtime: 15
  dev: 17
  build: 3
  unique: 27

Dependency findings
  0 errors
  0 warnings
  11 info

  INFO RC203 5 runtime dependencies have no version constraint: ...
  INFO RC204 Runtime dependencies use 9 exact pin(s), 1 range(s) and 5 without constraint. ...

CI References
  external: 7
  full-SHA pinned: 0
  mutable refs: 7
  local: 0

CI findings
  0 errors
  6 warnings
  0 info

  WARN RC220 External GitHub Action/workflow 'actions/checkout' is referenced by the mutable ref 'v2' ...

Report:
  C:\Projetos\ReproCheck\reprocheck-report.json
  C:\Projetos\ReproCheck\reprocheck-report.md
```

### The verdict

The report ends with one label, derived by explicit rules from the findings and
the reproduction. There is no score, no grade and no ranking: a number would
invent precision that was never measured.

| Verdict | When |
| --- | --- |
| `PASS` | a reproduction ran, every executed step succeeded, runtime checks were requested and every import succeeded, and **no warning is open** |
| `PARTIAL` | the reproduction succeeded as far as it went, but something important is unverified (runtime checks not requested, no import target) or a warning is still open |
| `FAIL` | a failure of the reproduction itself was observed: required Python missing or ambiguous, venv not created, installation failed, `pip check` conflict, a requested import failed or timed out, or the original project changed |
| `NOT_ATTEMPTED` | no `reproduce` run: `scan` collects static facts only |

`FAIL` requires proof. A project with static findings alone is never `FAIL` — a
mutable CI action reference is not a failed installation. Conversely `PASS` is
strict: an open warning keeps the verdict at `PARTIAL`, because a verified but
uncertain project is not a verified project.

> **`PASS` does not mean "scientifically reproducible".** It means only that the
> checks ReproCheck ran did succeed. Everything listed under **Not verified** —
> the full test suite, external datasets, remote URLs, CI action revisions, index
> state — is still unknown, and a `PASS` never says otherwise.

### Not verified

`reprocheck` separates *failed* from *not checked*, because a step that was never
executed has no result at all:

```text
## Not verified

These were not checked. They are not failures: the result is unknown.

- module imports and test collection: runtime checks were not requested (--runtime-checks)
- full test suite: ReproCheck never executes tests; only collection is attempted, and only with --runtime-checks
- external datasets and model downloads: never accessed; ReproCheck does not fetch data
- remote URLs and VCS references: not fetched; only their literal form is recorded
- CI action revisions: mutable action references are recorded but never resolved to a commit
- published package index and network services: installations resolve whatever the index serves at run time; no other service is contacted
```

The same list is in the JSON under `verdict.not_verified`, with the reason for
each item.

### Recommended next actions

Each finding ID maps to one static sentence: what to look at next, never what to
change automatically. There is no model, no ranking and no auto-fix, so the same
findings always produce the same recommendations.

```text
- **RC150** — Reproduce with VCS metadata available if the exact package version matters, or record the released version explicitly.
- **RC220** — Consider pinning the external GitHub Action/workflow to a full commit SHA if immutable CI inputs are required.
- **RC401** — Inspect the installation log before changing dependencies; the cause may be the index, the interpreter or the project itself.
```


### Report

```json
{
  "reprocheck_version": "0.7.0",
  "report_schema_version": "6",

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
  "dependencies": {
    "declarations": [
      {
        "name": "numpy",
        "raw_name": "numpy",
        "specifier": "==1.23.5",
        "source": "pyproject",
        "group": "runtime",
        "kind": "runtime",
        "marker": null,
        "extras": [],
        "file": "pyproject.toml",
        "line": null,
        "raw": "numpy==1.23.5",
        "reference": null,
        "reference_kind": null,
        "vcs_ref": null,
        "vcs_commit": null
      }
    ],
    "includes": [],
    "summary": { "runtime": 15, "dev": 17, "test": 0, "optional": 0, "build": 3, "constraint": 0, "unique_packages": 27 }
  },
  "workflow_references": [
    {
      "file": ".github/workflows/ci.yml",
      "line": 12,
      "raw": "actions/checkout@v4",
      "target": "actions/checkout",
      "ref": "v4",
      "reference_type": "action",
      "job": "build",
      "step": null,
      "is_local": false,
      "is_sha": false,
      "is_mutable": true
    }
  ],
  "reproduction": {
    "attempted": true,
    "workspace": null,
    "workspace_kept": false,
    "source": "C:\\Users\\you\\AppData\\Local\\Temp\\reprocheck\\<run-id>\\source",
    "original_project": "C:\\Projetos\\example",
    "network_enabled": false,
    "runtime_checks_enabled": true,
    "python": { "selected": "3.11.9", "executable": "C:\\Python311\\python.exe", "reason": "lowest installed version satisfying >=3.11" },
    "venv": { "path": "...\\venv", "python_version": "3.11.9", "initial_pip_version": "pip 24.0", "created": true },
    "installation": { "strategy": "project", "command": ["python", "-m", "pip", "install"], "exit_code": 0, "success": true, "stdout_path": ".../logs/install.stdout.log" },
    "pip_check": { "ran": true, "clean": true, "conflict_count": 0, "conflicts": [] },
    "installed_distribution": { "name": "example", "version": "1.2.3", "looks_like_fallback": false, "note": null },
    "runtime_checks": {
      "enabled": true,
      "import_discovery": null,
      "imports": [{ "module": "example", "imported": true, "exit_code": 0, "timed_out": false, "duration_seconds": 0.2 }],
      "pytest_collection": { "available": false, "ran": false, "success": null, "collected": null, "reason": "pytest is not installed in the reproduced environment" }
    },
    "original_project_unchanged": true,
    "integrity": { "git_head_before": "9f1c2b7", "git_head_after": "9f1c2b7", "tracked_files": 221, "changed_paths": [] },
    "completed_steps": ["static scan", "workspace created", "project copied", "venv created", "installation succeeded", "pip check completed", "installed version: 1.2.3", "import smoke test: 1 candidate(s)"]
  },
  "verdict": {
    "status": "PARTIAL",
    "reasons": [
      "2 open warning(s): RC150, RC220",
      "pytest is not installed in the reproduced environment, so test collection was skipped"
    ],
    "not_verified": [
      { "item": "test collection", "reason": "pytest is not installed in the reproduced environment" },
      { "item": "full test suite", "reason": "ReproCheck never executes tests; only collection is attempted, and only with --runtime-checks" }
    ]
  },
  "facts": { "package_manager_signals": [], "readme_references": [], "absolute_paths": [], "file_references": [], "tools": [], "gitignore": {}, "project_metadata": { "name": "example", "dependencies": [], "dynamic_fields": ["version"], "version_providers": ["setuptools-git-versioning"] } }
}
```

`report_schema_version` is `"6"`: V0.3 added the `dependencies` section, V0.4
added `workflow_references`, V0.5 added the optional `reproduction` section,
V0.6 added `runtime_checks` inside it, and V0.7 added the `verdict` block, which
is always present. The `reproduction` section only appears when `reproduce` was
run. The V0.1 and V0.2 keys are unchanged, and V0.2 only added
`report_schema_version`, `facts` and `confidence` inside each finding.


Every Python version declaration is always recorded separately, per source.
Which one wins is never decided: the checks only state whether the declarations
can be satisfied together.

### Markdown report

`reprocheck-report.md` is a projection of the same data for humans. It is
deterministic, evidence-first, and it omits sections that would be empty.
Sections, in order: `Summary`, `What worked`, `Problems found` (grouped by
severity, each finding with ID, severity, confidence, message, evidence and
location), `Not verified`, `Environment`, `Reproduction`, `CI`, `Dependencies`,
`Files and configuration`, `Recommended next actions`, `Technical details`.

Abridged example:

````markdown
# ReproCheck Report

## Summary

- Project: quartz-solar-forecast
- Path: C:\Projetos\OpenClimateFix\open-source-quartz-solar-forecast
- Scan time: 2026-09-27T16:31:04+00:00
- ReproCheck version: 0.7.0
- Reproduction verdict: **PARTIAL**
- Why:
  - 3 open warning(s): RC150, RC203, RC220

## What worked

- Git repository at commit c07ad7402598 (branch main, working tree clean).
- Python 3.11.16 was selected successfully.
- A virtual environment was created with Python 3.11.16.
- Project installation completed successfully (project, exit 0, 291.4s).
- pip check reported no broken requirements.
- 3 top-level module(s) imported successfully: `api`, `dashboards`, `quartz_solar_forecast`.
- The original source tree remained unchanged.

## Not verified

These were not checked. They are not failures: the result is unknown.

- test collection: pytest is not installed in the reproduced environment
- full test suite: ReproCheck never executes tests; only collection is attempted, and only with --runtime-checks
- external datasets and model downloads: never accessed; ReproCheck does not fetch data

## Reproduction

- Installation: PASS (strategy: project, exit 0, 291.4s)
- pip check: PASS (no broken requirements)
- Installed distribution: quartz-solar-forecast 0.0.1 — looks like a fallback version
- Imports: 3/3 top-level modules imported successfully
- Pytest collection: not run — pytest is not installed in the reproduced environment
- Original source tree: unchanged

## Recommended next actions

- **RC150** — Reproduce with VCS metadata available if the exact package version matters, or record the released version explicitly.
- **RC220** — Consider pinning the external GitHub Action/workflow to a full commit SHA if immutable CI inputs are required.
````

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

### Dependency declarations (V0.3)

Sources read: `pyproject.toml` (`project.dependencies`,
`project.optional-dependencies`, `dependency-groups`, `build-system.requires`,
Poetry tables) and `requirements*.txt`, including local `-r`/`-c` includes.

| ID | Severity | Confidence | Rule |
| --- | --- | --- | --- |
| RC200 | warning | high | The same package has two different exact pins in a scope that shares one environment |
| RC201 | error / warning | high / medium | No version satisfies both declarations (warning + medium when an opt-in extra is involved) |
| RC202 | info / warning | high / medium | The same package is declared more than once: identical (info) or compatible but different (warning) |
| RC203 | info | high | A runtime dependency has no version constraint |
| RC204 | info | high | Runtime dependencies mix exact pins, ranges and no constraint |
| RC205 | info | medium | A dependency is declared differently for runtime and dev, but stays compatible |
| RC206 | warning | high | A `-r`/`--requirement` include does not exist in the project |
| RC207 | warning | high | A `-c`/`--constraint` file does not exist in the project |
| RC208 | info | high | A dependency comes from a direct URL or from a VCS reference pinned to a commit |
| RC209 | warning | high | A dependency comes from a VCS reference that is not a commit (branch, tag or none) |
| RC210 | warning | high | A dependency points to a local path that is not part of the repository |

Two declarations are only required to be satisfiable together when they belong
to the same **scope**:

| Scope | Contents |
| --- | --- |
| `runtime+dev` | runtime plus `dev`/`test` groups: installing the dev group normally installs the project |
| `optional:<extra>` | one optional extra; opt-in, so it is never compared with runtime |
| `constraint` | `-c` constraint files, compared with the runtime scope |
| `build` | `[build-system].requires`, installed in an isolated environment and never compared |

Anything a check cannot prove is not reported. In particular, a wider range
(`>=1` against `>=1.2`) is compatible, an unbounded declaration is compatible
with everything, and a `!=` exclusion can hide versions no candidate happens to
cover, so such a pair is reported as divergent rather than impossible.

### GitHub Actions references (V0.4)

| ID | Severity | Confidence | Rule |
| --- | --- | --- | --- |
| RC220 | warning | high | An external action or reusable workflow is referenced by something other than a full 40-character commit SHA |
| RC221 | warning | high | The same target is referenced with more than one distinct ref |
| RC222 | warning | high | A local `uses: ./...` action or workflow does not exist in the repository |
| RC223 | warning | high | A `docker://` action is referenced by tag instead of digest |

ReproCheck knows only three things about a remote reference: whether the ref is
a full commit SHA, whether the same target is used with different refs, and
whether a local target exists. It never states that a version is obsolete or
unsafe, because that would require external and temporal knowledge.

The version-constraint engine behind RC103/RC200/RC201 distinguishes three
outcomes, and only the first may become an error:

| Outcome | Meaning | Requirement |
| --- | --- | --- |
| proof of incompatibility | no version can satisfy both | interval reasoning over `==`, `>=`, `>`, `<=`, `<`, or a single pin explicitly excluded by `!=` |
| witness of compatibility | one concrete version satisfies both | a real version is produced and quoted as evidence |
| unknown | neither | never reported as a conflict |


### Confidence

Every finding carries `confidence`, derived from objective criteria only:

- `high` — both sides of the statement were read directly from the repository
  (for example a `.python-version` value and a `requires-python` value).
- `medium` — the statement is true but admits an innocent explanation, such as
  a path created or downloaded at runtime, or a stale-looking lockfile.
- `low` — not used in V0.2.

Severities never imply a bug verdict. A `warning` means "this deserves a human
look", not "this is broken".

### Version provenance (V0.6)

| ID | Severity | Confidence | Rule |
| --- | --- | --- | --- |
| RC150 | warning | high | The distribution version is declared dynamic and provided from Git/VCS metadata, so a reproduction copy without `.git` cannot derive it |

Providers detected: `setuptools-git-versioning`, `setuptools_scm`, `hatch-vcs`
and `poetry-dynamic-versioning`, from `[build-system] requires` and from the
corresponding `[tool.*]` sections. The check states the declaration, never that
the build will fail.

## Reproduce: a controlled installation attempt

`scan` never executes anything from the project. `reproduce` does, inside an
isolated temporary workspace:

```powershell
reprocheck reproduce C:\Projetos\some-project
reprocheck reproduce C:\Projetos\some-project --network --keep-workspace
```

| Option | Effect |
| --- | --- |
| `--network` | Allow the **installation step only** to reach a package index. Off by default: `pip` runs with `--no-index` |
| `--keep-workspace` | Keep the temporary workspace even after a successful attempt |
| `--runtime-checks` | Also import the installed modules and collect tests. **Executes project code**, so it is off by default |
| `--json <arquivo>` | JSON report destination, as in `scan` |
| `--markdown <arquivo>` | Markdown report destination, as in `scan` |
| `--verbose` | Print the steps taken and the log locations |


The pipeline is:

```
static scan → isolated workspace (copy) → Python selection → virtual environment
→ installation → pip check → installed version
→ [import smoke test] → [pytest collection]        (only with --runtime-checks)
→ integrity verification of the original project
```

The project is **copied** to `<TEMP>/reprocheck/<run-id>/source`, excluding
`.git`, environments, caches and build output. The virtual environment is
created at `<TEMP>/reprocheck/<run-id>/venv`, never inside the project. Before
and after the attempt, the size and mtime of every file plus the Git HEAD and
status are compared: any change is reported as **RC406**, an error about
ReproCheck itself.

Installation strategies, in order:

| Strategy | Command | When |
| --- | --- | --- |
| `project` | `pip install <workspace>/source` | the project declares a distribution (PEP 621, Poetry or setup.py). Non-editable on purpose: it exercises the declared build backend |
| `requirements` | `pip install -r <workspace>/source/requirements.txt` | no installable project, but a `requirements.txt` exists |
| none | — | neither: the attempt stops with RC403 instead of guessing |

Workspace cleanup: a successful attempt is deleted, a failed one is kept because
that is where the evidence lives, and `--keep-workspace` always keeps it.

**A virtual environment is not a security sandbox.** Installing a project runs
its build backend with the current user's privileges, and `--runtime-checks`
additionally imports the package and lets pytest execute `conftest.py`, plugins
and test-module imports. Read `docs/reproduction-safety.md` before running
`reproduce` on anything you would not `pip install` yourself.

### Runtime checks

With `--runtime-checks`, after a successful installation:

1. the import target is **discovered**, never guessed from the distribution
   name: `importlib.metadata` lists the files of the installed distribution and
   the top-level modules are derived from them, discarding `.dist-info`,
   `.data` and documentation or test directories. When no unambiguous candidate
   exists, the report says so and no import is attempted;
2. each candidate is imported in its own process with a 30 second timeout, and
   the process tree is killed on timeout;
3. the installed version is read with `importlib.metadata.version`;
4. pytest collection runs **only if pytest is already installed in the
   reproduced environment**. ReproCheck never installs it. Test functions are
   never executed.

### Reproduction findings

| ID | Severity | Confidence | Rule |
| --- | --- | --- | --- |
| RC400 | error | high | `pip check` reported at least one conflict in the reproduced environment |
| RC401 | error | high | The installation failed; the command, exit code, log path and a short error excerpt are reported |
| RC402 | error | high | The required Python version is not installed locally |
| RC403 | warning | high | Neither an installable project nor a `requirements.txt` was found |
| RC404 | info | high | The install output proves the dependencies were not available locally, so `--network` is required |
| RC405 | error | high | The Python version could not be selected deterministically |
| RC406 | error | high | The analysed project changed during the attempt (a ReproCheck error) |
| RC407 | error | high | The virtual environment could not be created |
| RC500 | error | high | An installed top-level module could not be imported |
| RC501 | warning | high | `pytest --collect-only` failed. Warning, not error: it blocks the test suite but not the use of the package |
| RC502 | error | high | A module import exceeded the per-module timeout |


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

`docs/benchmark-openclimatefix.md` records how each version performs against a
real external repository, including the problems it does **not** detect, and
`docs/reproduction-safety.md` documents the isolation model and its limits.

## Current limitations

- Python projects only.
- Dependencies are read from `pyproject.toml` and `requirements*.txt` only.
  `setup.py`, `setup.cfg`, `Pipfile`, `environment.yml` and Conda files are not
  parsed, and `constraints.txt` is only read when a `-c` include points to it.
- Poetry constraints are translated to PEP 440 (`^`, `~`), but Poetry-specific
  sources (git dependencies, path dependencies, multiple constraints) are not
  modelled.
- Workflow parsing is line-based; YAML anchors, multi-line values and
  `env`-based indirection are not resolved.
- README extraction is textual; it records commands, it does not interpret
  them, and it may miss commands written outside code blocks.
- `.gitignore` comparison is an exact match on the normalised pattern; glob
  semantics (`**/build/`, `*.log`) are not evaluated, and nested `.gitignore`
  files are ignored.
- Path scanning is regex-based over `.py`, `.toml`, `.yaml`, `.yml`, `.json`,
  `.ini` and `.cfg` files; references built at runtime are not resolved.
- Static findings prove that two declarations disagree, never that an install
  would fail. Only `reproduce` answers the second question, and it does so by
  installing.
- Incompatibility is only claimed with a proof. Complex operators (`~=`,
  wildcards, `!=` combinations, prerelease exclusions) may stay undecided, and an
  undecided pair is reported as info, never as an error.
- Nothing compares a CI ref with a known-good value, and no remote is queried to
  check whether a commit or tag still exists.
- `reproduce` stops after `pip check` plus the optional runtime steps: tests are
  collected, never executed, and there is no plugin or marker configuration.
- Import discovery only sees the files the distribution declares in
  `importlib.metadata`. A namespace package spread over two wheels, a lazy
  plugin registered at runtime, or an entry point that adds an import target are
  all invisible to it.
- Collection needs pytest already in the reproduced environment. Installing
  development dependencies to enable the step is out of scope, so projects whose
  runtime install excludes pytest skip the step silently.
- `reproduce` copies the project without `.git`, so a version derived from Git
  tags cannot be reproduced. RC150 reports the declaration, but there is no mode
  that preserves Git metadata yet.
- Installation time is bounded (30 minutes by default) and a timeout is reported
  as a failure with exit code 124.
- A virtual environment isolates versions, not authority. `reproduce` runs
  untrusted build backends with the current user's privileges.
- The verdict is a label derived from the rules above, not an investigation. It
  cannot know whether a `PASS` project produces the same numbers as the original
  run, and it is only as good as the checks behind it.
- `PASS` requires no open warning, which is a deliberately strict bar: a project
  with a single mutable CI action reference is `PARTIAL`.
- The Markdown report repeats the JSON content for humans; it is not a
  different analysis, and it adds no conclusion of its own.
- No AI, no auto-fix and no scoring. The recommendations are static sentences
  attached to finding IDs, not advice derived from the project. The network is
  only used by an explicit `--network` during installation.

## Roadmap

- `0.8` — baseline reports: store a report and diff it against a new scan to
  show reproducibility drift over time.
- `0.9` — `setup.cfg`, `Pipfile` and Conda dependency sources, PEP 735
  `include-group` resolution, and non-Python ecosystems (Node).
- later — an explicit opt-in mode that preserves Git metadata in the
  reproduction copy, so a tag-derived version can be reproduced, plus entry
  point based import discovery.

