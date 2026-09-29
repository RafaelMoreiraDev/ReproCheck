# ReproCheck

## What is ReproCheck?

> ReproCheck audits whether a Python project can be reproduced, records evidence
> about what works and what fails, and can propose — or safely apply — a small
> set of deterministic fixes.

It is a **read-only scanner for reproducibility**. Given a project directory, it
answers one question with evidence:

> If someone receives this repository today, is it possible to reconstruct the
> exact environment that produced its results — and does the project itself say
> how?

Most "it works on my machine" problems are not code bugs. They are missing or
contradictory facts: no pinned Python version, two competing package managers, a
documented install command that no longer exists, a CI action referenced by a
tag, an uncommitted working tree at the moment results were produced. ReproCheck
collects those facts into a deterministic JSON report and projects the same
content into a Markdown report a person can read without knowing any check ID.

## Install

```powershell
python -m pip install reprocheck-cli
```

The distribution on PyPI is `reprocheck-cli`; the command is `reprocheck` and
the importable package is `reprocheck`.

Requires Python 3.11 or newer. There are two runtime dependencies,
[`packaging`](https://pypi.org/project/packaging/) for version reasoning and
[`PyYAML`](https://pypi.org/project/PyYAML/) for reading Conda environment
files.

## The six commands

```powershell
# 1. static audit: reads the project, writes JSON + Markdown reports
reprocheck scan .

# 2. controlled installation in an isolated copy (see the safety note below)
reprocheck reproduce .

# 3. runtime checks: import the installed modules, collect the tests
reprocheck reproduce . --runtime-checks

# 4. deterministic fix suggestions, nothing is written
reprocheck suggest .

# 5. show exactly what would be written
reprocheck fix . --suggestion FIX-RC140-001

# 6. write it, verify it, and keep a record you can roll back
reprocheck fix . --suggestion FIX-RC140-001 --apply
```

Reports go to a per-user state directory
(`%LOCALAPPDATA%\reprocheck` on Windows, `~/.local/state/reprocheck` elsewhere),
never inside the analysed project. Use `--json`, `--markdown` or `--output-dir`
to choose a destination.

Comparing two points in time:

```powershell
reprocheck baseline save .\report.json --output .\baseline.json
reprocheck scan .
reprocheck baseline compare .\baseline.json .\reprocheck-report.json
```

Each command writes two files, one JSON and one Markdown:

```text
reprocheck-report.json   the structured source of truth
reprocheck-report.md     the same facts, for a person
```

Exit codes: `0` done, `1` verdict is `PARTIAL`, `2` verdict is `FAIL`, `3`
operational error, `5` a fix was stale or not applicable, `6` a fix failed and
was rolled back, `7` a rollback failed.

## Safety

This is part of the product, not a footnote.

| Command | Runs the project's code? | Writes to the project? |
| --- | --- | --- |
| `scan` | **no** | no |
| `suggest` | **no** | no |
| `baseline compare` | **no** | no |
| `reproduce` | yes — its build backend, in a copy | no |
| `reproduce --runtime-checks` | yes — imports and `pytest --collect-only` | no |
| `fix` (dry run) | no | no |
| `fix --apply` | no | **yes**, one file, named by you |

- `scan`, `suggest` and `baseline compare` never execute anything from the
  analysed project, and never write inside it.
- `reproduce` executes the project's build backend with your own privileges,
  inside a workspace copy. `--runtime-checks` additionally imports the installed
  package and lets pytest import `conftest.py`, plugins and test modules. No test
  function is ever executed.
- **A virtual environment is not a security sandbox.** Run untrusted code in a
  container or a VM.
- `fix` writes only for the suggestion id you type, only with `--apply`, only
  for a `SAFE` suggestion, and only after checking that the file has not changed
  since the proposal was built. Read `docs/reproduction-safety.md` first.

## What ReproCheck is not

- Not a tool that confirms **scientific results**. It never runs a study, a
  benchmark or a model.
- Not a container manager, and not an experiment tracker.
- Not a universal dependency solver: it does not choose versions, and it never
  picks one for you.
- Not a Conda tool by default. `scan` **inspects** Conda environment
  declarations; it never runs conda, contacts a channel, solves anything or
  writes a lockfile. Reproducing a Conda environment is a separate, opt-in
  step (`reproduce --conda --network`), and it runs a third-party solver that
  downloads and executes packages. A project that uses Conda is checked for
  contradictions by default, and for reproducibility only when asked.
- Not proof that a scientific study is reproducible.

A `PASS` verdict means only that **the checks which ran succeeded**. Everything
under **Not verified** — the full test suite, external datasets, remote URLs, CI
action revisions, package index state — stays unknown, and the report says so.

## Validated on real projects

Five public repositories, one commit each, cloned outside this repository and
left untouched. Full evidence, including every warning reviewed by hand:
[`docs/external-validation.md`](docs/external-validation.md).

| Project | Commit | Python files | Scan | Suggest | Reproduce |
| --- | --- | --- | --- | --- | --- |
| [pint](https://github.com/hgrecco/pint) | `e4042bbe5c66` | 110 | 0.53 s, 0 errors, 12 warnings | 1 safe, 7 review, 2 manual | fails: version comes from Git metadata |
| [tqdm](https://github.com/tqdm/tqdm) | `9cf5a12b1f95` | 68 | 0.36 s, 0 errors, 11 warnings | 1 safe, 9 review, 2 manual | fails: version comes from Git metadata |
| [astropy](https://github.com/astropy/astropy) | `592070633f16` | 1006 | 3.80 s, 0 errors, 9 warnings | 1 safe, 0 review, 4 manual | fails: version comes from Git metadata |
| [mne-python](https://github.com/mne-tools/mne-python) | `47d5be239f12` | 929 | 11.06 s, 0 errors, 38 warnings | 1 safe, 26 review, 3 manual | fails: version comes from Git metadata |
| [napari](https://github.com/napari/napari) | `4b1f6dd779c6` | 1029 | 2.20 s, 0 errors, 8 warnings | 1 safe, 3 review, 2 manual | installed, `pip check` clean, 2 modules imported |

Five scans, five suggests and eleven reproductions produced **no crash, no
traceback and no unexpected write**: every clone finished with a clean
`git status`. All 78 warnings were reviewed against the cited source, six
classes of false positive were found and fixed with regression tests, and one
`SAFE` suggestion proved lossy for CRLF files and was fixed too. A project that
fails to install is a fact about that project, not about ReproCheck: the four
failures above all say the same true thing, that a copy without `.git` cannot
derive a version from tags.

This is a validation on five repositories at one point in time. It is **not** a
claim of compatibility with every Python project, and it says nothing about
whether any of these projects reproduces its own results.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/reproduction-safety.md](docs/reproduction-safety.md) | what each step executes, network policy, Git policy, the isolation model and its limits |
| [docs/external-validation.md](docs/external-validation.md) | the five real projects, every finding classified by hand, gaps and metrics |
| [docs/benchmark-openclimatefix.md](docs/benchmark-openclimatefix.md) | the history of the checks against one repository over successive versions |
| [CHANGELOG.md](CHANGELOG.md) | what this release contains and what it still cannot do |

The rest of this README is the reference: the layers, every check, the report
format, the verdict rules, the suggestion engine, the fix path and the
limitations.

## How it is organised

ReproCheck separates six layers, and never mixes them:

| Layer | Question | Module |
| --- | --- | --- |
| **FACT** | What was observed, and where? | `reprocheck.facts`, `reprocheck.scanners` |
| **CHECK** | Which deterministic rule does that fact break? | `reprocheck.checks` |
| **FINDING** | What can be stated, with which evidence and confidence? | `reprocheck.models.Finding` |
| **VERDICT** | What do all of those facts add up to, and what is still unknown? | `reprocheck.models.verdict`, `reprocheck.checks.verdict` |
| **SUGGESTION** | Is there a deterministic, safe change that would remove this finding? | `reprocheck.suggest` |
| **APPLICATION** | Can that one change be written, verified and undone? | `reprocheck.fix` |

A check is a pure function of the facts. It cannot read the filesystem, run code
or guess: if a conclusion cannot be proven, no finding is emitted. The verdict is
a pure derivation of the findings: it adds no new claim, and it never produces a
numeric score. A suggestion is a pure derivation of a finding and the facts it
came from. Only `fix` writes, and only with `--apply`.


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
choose (or in the per-user state directory, below). Nothing is written inside
the analysed project.

Git is inspected with read-only commands only (`rev-parse`, `status
--porcelain`), executed with `--no-optional-locks` so that even the Git index is
left untouched.

`reproduce` executes the project's build backend on a **copy**, in a workspace
outside the project, and the original is verified before and after. `fix` is the
only command that writes to the project, one named suggestion at a time, only
with `--apply`, and only for a `SAFE` suggestion with a precondition hash. See
"Reproduce", "Fix" below and `docs/reproduction-safety.md`.

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
reprocheck reproduce C:\Projetos\some-project --network --runtime-checks
reprocheck suggest C:\Projetos\some-project
reprocheck fix C:\Projetos\some-project --suggestion FIX-RC140-001
reprocheck baseline save reprocheck-report.json --output baseline.json
reprocheck baseline compare baseline.json reprocheck-report.json
```

or, without installing the console script:

```powershell
python -m reprocheck scan C:\Projetos\some-project
python -m reprocheck reproduce C:\Projetos\some-project
python -m reprocheck suggest C:\Projetos\some-project
python -m reprocheck fix C:\Projetos\some-project --suggestion FIX-RC140-001
python -m reprocheck baseline compare baseline.json reprocheck-report.json
```

`scan` options:

| Option | Description |
| --- | --- |
| `--json <arquivo>` | JSON report destination. Default: the state directory (below) |
| `--markdown <arquivo>` | Markdown report destination. Default: the state directory |
| `--output-dir <diretorio>` | Directory for both files when the two above are absent |
| `--verbose` | Also print HEAD, package manager hints and README commands |

`reproduce` takes the same three destination options, plus `--network`,
`--keep-workspace`, `--runtime-checks` and `--verbose`. `suggest` takes the three
destination options and nothing else — it has no `--apply`. `fix` takes the three
destination options, `--suggestion <ID>` or `--rollback <record-id>` (one of the
two is required) and `--apply`.

### Where files are written

With **no** destination given, reports go to a per-user state directory, in a
sub-directory named after the analysed project:

| Platform | Location |
| --- | --- |
| override | `REPROCHECK_STATE_DIR` (used by the test suite and by CI) |
| Windows | `%LOCALAPPDATA%\reprocheck` |
| other | `$XDG_STATE_HOME/reprocheck`, or `~/.local/state/reprocheck` |

```text
<state>/reprocheck/<name>-<8 hex of the project path>/reprocheck-report.json
<state>/reprocheck/<name>-<8 hex of the project path>/reprocheck-report.md
<state>/reprocheck/<name>-<8 hex of the project path>/reprocheck-diff.json
<state>/reprocheck/<name>-<8 hex of the project path>/reprocheck-diff.md
<state>/reprocheck/<name>-<8 hex of the project path>/baseline.json
<state>/reprocheck/<name>-<8 hex of the project path>/reprocheck-suggestions.json
<state>/reprocheck/<name>-<8 hex of the project path>/reprocheck-suggestions.md
```

**Breaking change in V0.8:** V0.7 wrote the reports to the current working
directory, which silently dirtied a repository when ReproCheck ran from inside
the project it was analysing. Nothing is now written inside a project unless you
ask for it with `--json`, `--markdown`, `--output-dir` or `--output`. Explicit
paths are used verbatim, so a script that passes its own destination keeps
working unchanged. The path of both files is always printed.

### Exit codes

| Code | `scan` | `reproduce` | `suggest` | `fix` | `baseline save` / `compare` |
| --- | --- | --- | --- | --- | --- |
| `0` | report written | verdict is `PASS` | proposals written | dry run shown, change applied and validated, or rollback succeeded | done (a comparison returns `0` whether or not anything changed) |
| `1` | — | verdict is `PARTIAL` | — | — | — |
| `2` | — | verdict is `FAIL` | — | — | — |
| `3` | path unusable, or a report could not be written | same | same | same | baseline missing, invalid, unknown schema, refusing to overwrite, or a different project |
| `5` | — | — | — | stale or not applicable; nothing was written | — |
| `6` | — | — | — | the write failed, the rollback restored the previous bytes | — |
| `7` | — | — | — | the rollback itself failed | — |

`2` is also what argparse uses for a malformed command line, which is why
operational errors use `3`. A change is a fact, not an error: `compare` never
signals "something changed" through the exit code, and neither `suggest` nor
`fix` signals "there was something to fix" through it.


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
  C:\Users\you\AppData\Local\reprocheck\example-1a2b3c4d\reprocheck-report.json
  C:\Users\you\AppData\Local\reprocheck\example-1a2b3c4d\reprocheck-report.md
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
````

## Baselines: what changed (V0.8)

A **baseline** is a report kept as the reference of a later comparison. The
comparison is a pure function of the two reports: nothing is re-run, nothing is
reinstalled, and the analysed project is never opened.

```powershell
# 1. produce a report and keep it as the reference
reprocheck scan C:\Projetos\some-project --json .\report.json
reprocheck baseline save .\report.json --output .\baseline.json

# 2. later, compare whatever the same command produces now
reprocheck scan C:\Projetos\some-project --json .\report.json
reprocheck baseline compare .\baseline.json .\report.json
```

`save` refuses to replace an existing baseline unless `--force` is given, and
validates before accepting: valid JSON, produced by ReproCheck, a known
`report_schema_version`, and the minimum keys the comparison reads. A baseline
may also be an older report (schemas `1`–`6` are accepted; the sections an old
schema does not have are reported as "not compared" in the terminal). An unknown
schema, now or in the future, is refused with a clear error rather than compared
silently.

The report inside a baseline is stored **verbatim**: a baseline is evidence.

### What a comparison looks at

| Domain | Identity | Compared attributes |
| --- | --- | --- |
| findings | `id` + file + normalised evidence | title, severity, confidence, category, message |
| dependencies | name + kind + group | specifier, reference kind, reference, VCS ref/commit, source |
| Python | declaration source | value, file |
| CI | target + file | ref, reference type, immutability |
| reproduction | one key per observed fact | selected Python, strategy, install success, `pip check`, installed distribution, each import, collection, integrity |

Identity is what decides whether two entries are the same thing. Two entries
with different identities are two facts, whatever the wording: an aggregated
finding whose set of packages changed appears as one **resolved** plus one
**added**, not as an edit. The same applies to a dependency that moved from the
`dev` group to the project group.

### What a comparison ignores

Timestamps, installation and command durations, the temporary workspace and its
run id, log paths, the interpreter executable, the order of two independent
lists, and **line numbers**: inserting one line renumbers every finding below
it. Two equivalent runs, taken minutes apart, must compare as *no material
changes*, and the tests assert exactly that.

The verdict movement is reported with `previous` and `current` only. ReproCheck
does not call it an improvement or a regression: the two reports describe two
points in time, and a `PASS` today with a `FAIL` last month can mean a different
commit, a different machine or a different index.

### Comparison outputs

`reprocheck-diff.json` (structured) and `reprocheck-diff.md` (for humans), in the
state directory or wherever `--json`/`--markdown`/`--output-dir` point:

```markdown
# ReproCheck Baseline Comparison

## Summary

- Baseline: demo
  commit `0123456789ab`
  scanned 2026-01-01T00:00:00+00:00
- Current: demo
  commit `89abcdef0123`
  scanned 2026-06-30T23:59:59+00:00

Verdict: PARTIAL → FAIL

Material changes: 3

## Verdict

The verdict changed from PARTIAL to FAIL.

## New problems

RC401 — NEW IN THE COMPARISON
The installation failed.

## Changed facts

### Dependencies

- **numpy (project)** — specifier: `specifier===1.23.5` → `specifier=>=1.26,<2`
```

Terminal output, and what happens when nothing changed:

```text
ReproCheck baseline comparison

Verdict:
  PARTIAL -> PARTIAL

Changes:
  1 finding(s) added
  1 finding(s) resolved
  1 dependency change(s)
```

```text
No material reproducibility changes detected.
```



### Report

```json
{
  "reprocheck_version": "0.10.0",
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
- ReproCheck version: 0.10.0
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

## Suggest: deterministic fix proposals (V0.9)

> **Suggest never modifies the analysed project.** It reads the project, builds
> what a change *would* look like in memory, and writes the proposal to the state
> directory. There is no `reprocheck fix`, no `--apply` and no prompt.

```powershell
reprocheck suggest C:\Projetos\some-project
```

```text
ReproCheck suggestions

Safe fixes:       1
Review required:  6
Manual only:      3
With a patch:     1
No proposal:      1

  [FIX-RC140-001] Add the missing artifact patterns to .gitignore
      file: .gitignore

No files were modified.

Report:
  ...\reprocheck-suggestions.json
  ...\reprocheck-suggestions.md
```

A finding never implies a fix. Every finding that is a problem gets exactly one
of three answers, and the answer comes from a fixed table of rules: no model, no
inference, no network.

| Classification | Answer | Criteria |
| --- | --- | --- |
| `SAFE` | `FIX_AVAILABLE` | a local, additive, reversible text edit; no semantic choice; no logic touched; the result is unambiguous |
| `REVIEW_REQUIRED` | `REVIEW_REQUIRED` | a concrete direction exists, but a value or a decision does not |
| `MANUAL_ONLY` | `NO_AUTOFIX` | any patch would be speculation about the project |

| Finding | Classification | Patch? | Why |
| --- | --- | --- | --- |
| RC140 | `SAFE` | yes | the patterns are proven missing by the check itself, and the edit only appends lines |
| RC220 | `REVIEW_REQUIRED` | no | a pinned SHA is the answer, but which commit `v4` points at is external and temporal |
| RC116 | `MANUAL_ONLY` | no | a lockfile resolves real dependencies against an index |
| RC150 | `MANUAL_ONLY` | no | the fix is a release-process decision, and the fallback version is not derivable here |
| RC203 | `MANUAL_ONLY` | no | a version bound is a maintenance decision; an invented pin is worse than none |
| RC121 | `MANUAL_ONLY` | no | the correct path cannot be inferred |
| RC130 / RC131 / RC132 | `MANUAL_ONLY` | no | ReproCheck cannot know whether the README or the referenced file is wrong |

`SAFE` means the *change* is safe to review, not that a machine should apply it.
Every suggestion carries `requires_user_approval: true`, and only a `SAFE`
suggestion with a diff is marked as a candidate for a future automatic
application.

Only **RC140** produces a patch in V0.9, and it is one patch for the whole
finding, not one per pattern:

```diff
--- a/.gitignore
+++ b/.gitignore
@@ -11,3 +11,9 @@
 __pycache__/
 .cache.sqlite
 *.egg-info
+.mypy_cache/
+.pytest_cache/
+.ruff_cache/
+.venv/
+build/
+dist/
```

The diff is a standard unified diff built in memory, with the line endings of
the current file preserved and the conventional `\ No newline at end of file`
marker when the file has no final newline. A `.gitignore` with a byte-order mark
or with content that is not plain UTF-8 gets **no** patch: a proposal whose
`before` cannot represent the current bytes exactly is not a proposal, it is a
guess. A project with no `.gitignore` at all gets no RC140 proposal either, since
creating one is a decision about the project.

Applying a proposal by hand makes the suggestion disappear: the second run finds
every pattern covered and proposes nothing. That is the intended behaviour and it
is tested.

`reprocheck-suggestions.json` records `suggestion_schema_version`, the project,
the counts and one entry per suggestion (`suggestion_id`, `finding_id`, `kind`,
`title`, `safety`, `confidence`, `file`, `description`, `rationale`, `before`,
`after`, `unified_diff`, `requires_user_approval`, `can_auto_apply_later`,
`limitations`), plus a `no_proposal` list so that a problem without a rule is
visible instead of silently absent. Findings with no rule and `info` severity
are counted as informational, because they are observations, not problems.

## Fix: applying one SAFE suggestion (V0.10)

`reprocheck fix` is the only command in ReproCheck that writes to the analysed
project. Everything else is read-only. There is no "apply all", no prompt, and
no way to apply a suggestion that is not `SAFE` and carries a patch.

```text
reprocheck suggest  <project>                          # what could be fixed
reprocheck fix      <project> --suggestion FIX-RC140-001          # dry run
reprocheck fix      <project> --suggestion FIX-RC140-001 --apply  # write it
reprocheck fix      <project> --rollback 20260928T013339-FIX-RC140-001
```

A dry run is the default. It re-scans, regenerates the suggestions, and prints
the precondition hashes, the diff and `No files were modified.` Only `--apply`
writes anything.

| Exit | Meaning |
| --- | --- |
| `0` | dry run shown, or the change was applied and validated, or a rollback succeeded |
| `3` | operational error: the path is unusable, or a report could not be written |
| `5` | `STALE` or `NOT_APPLICABLE`: nothing was written |
| `6` | the write failed but the rollback restored the previous bytes |
| `7` | the rollback itself failed — a severe ReproCheck error, reported loudly |

`1` and `2` are not used: they are the `reproduce` verdicts.

### Gates before anything is written

1. the suggestions are **regenerated** from the project as it is now; a
   suggestion id from an older report is never trusted;
2. the requested id must exist;
3. `safety` must be `SAFE` — `REVIEW_REQUIRED` and `MANUAL_ONLY` are refused with
   *"This suggestion requires human judgment and cannot be applied
   automatically."*;
4. the suggestion must carry a diff and be marked `can_auto_apply_later`;
5. the file must exist;
6. the SHA-256 of the target's **exact bytes** must equal `before_sha256`, the
   precondition the rule computed when it built the proposal. A different hash
   means somebody wrote to the file in between: ReproCheck does not merge and
   does not overwrite, and the status is `STALE`.

### How the write happens

The bytes written are exactly the bytes the rule proposed — no formatter
rebuilds the file. The write goes to a temporary file **in the target's own
directory**, is flushed, `fsync`ed when the platform allows it, and then put in
place with `os.replace`, which is atomic. The original file mode is preserved.
The temporary file is removed if anything fails, and nothing is ever left next
to the project: no `.bak`, no temp file.

Before the write, the current bytes are copied to a backup **inside ReproCheck's
state directory**, never beside the project:

```text
<state>/reprocheck/<project>/applied/<record-id>/before.bin
<state>/reprocheck/<project>/applied/<record-id>.json
```

The record is the audit trail: project identity, suggestion and finding ids,
safety, file, timestamps, both hashes, the backup path, the outcome and the
rollback state. No secret is ever stored.

### What counts as success

After the write, two checks run, and a failure in either one is a rollback:

1. the file is read again and its SHA-256 must equal `after_sha256`;
2. the project is **re-scanned statically** and the finding must be gone.

Only then is the status `APPLIED`, with the message *"The project now contains
one intentional change."* — not "the project is fixed": every other finding is
untouched, and the tool says nothing about them. `reproduce` is never run after
a fix: that would install and execute project code, and it belongs to an
explicit, separate command.

If the write, the hash check or the re-scan fails, the backup is restored and
the restoration is verified. The status is then `ROLLED_BACK` (exit `6`) or, if
the restoration itself cannot be completed, `ROLLBACK_FAILED` (exit `7`) — which
is never hidden.

### Git

Git is not required. When the project is a repository, the HEAD and
`git status --porcelain` are recorded before and after with **read-only**
commands. ReproCheck never commits, branches, checks out, resets or cleans. If
any path other than the target changed during the operation, the report says so
as an anomaly; ReproCheck does not touch those files.

A target that already has uncommitted changes is not a reason to stop: the
proposal is built on the current bytes, so the precondition still matches, and
the run is annotated with *"Target file already has uncommitted changes."*

### Rolling back

```powershell
reprocheck fix <project> --rollback <record-id>
```

The rollback restores the bytes of `before.bin` **only if the file still matches
`after_sha256`**. If it does not, the status is `STALE` and nothing is written:
ReproCheck does not overwrite work done after the fix, and the backup stays
available. Applying a suggestion twice is impossible — after the first
application the same id no longer exists, so the second attempt is
`NOT_APPLICABLE`.

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

### Conda environment declarations (V0.12)

| ID | Severity | Confidence | Rule |
| --- | --- | --- | --- |
| RC230 | error | high | The Conda environment's Python and the project's `requires-python` accept no common version |
| RC231 | error | high | The same package is declared incompatibly in the environment and in the pip metadata |
| RC232 | info | high | The same package is declared differently but compatibly in the two |
| RC233 | info | high | A package is listed both as a Conda dependency and inside the `pip:` subsection |
| RC234 | error / warning | high | The file is a Conda environment by name but could not be parsed, or contains a structure that is not interpreted |
| RC235 | info | high | A `pip:` subsection exists but `pip` is not listed as a Conda dependency |

ReproCheck **reads** `environment.yml` and `environment.yaml` at the root. A
file named `environment-<something>.yml` is only read when the project names it,
in the README, the docs or a workflow: no YAML is guessed to be an environment.
YAML is parsed with a safe loader only, so a custom tag can never construct a
Python object, and a file that uses one is reported rather than executed.

Conda version syntax is not PEP 440. `numpy=1.26` means the 1.26 series, not
`numpy==1.26`, and `numpy=1.26=py311np123` ends in a build string that is not a
version at all. The original text is kept verbatim; a PEP 440 form is derived
only where the two accept the same set of versions, and a constraint that cannot
be expressed that way is left uncompared rather than guessed at. A `# [win]`
selector is a YAML comment, so it is recovered from the raw text: a dependency
that applies to one platform is never compared as if it applied to all of them.

**None of these checks has an opinion about Conda.** Using `defaults`, using
`conda-forge`, not pinning a dependency, having a `pip:` subsection, and having
no lockfile are all project decisions, and none of them produces a finding.
Every Conda finding is `MANUAL_ONLY`: choosing a Python version, a constraint or
a channel is a decision ReproCheck will not make for you.

The environment is read, not reproduced. When a project declares one, the report
lists it under **Not verified**: no conda process was run, no channel was
contacted and no environment was solved.

### Conda reproduction (V0.12)

`reproduce` uses pip. It can be asked to build the project's Conda environment
instead, and the choice is never made for you:

```powershell
# what the static scan already reads
reprocheck scan .

# refuse without network permission
reprocheck reproduce . --conda

# create the environment from environment.yml
reprocheck reproduce . --conda --network

# a project with more than one environment file
reprocheck reproduce . --conda --network --conda-env environment-dev.yml

# pin the manager, and add the runtime checks
reprocheck reproduce . --conda --network --conda-manager micromamba --runtime-checks
```

Without `--conda` nothing changes: the pipeline is the pip one it has always
been. The two strategies are never merged, and every report says which one
produced it, so a baseline comparison shows a strategy change as a strategy
change rather than as a change of versions.

Three managers are supported, discovered in the order **micromamba, mamba,
conda**, and the report records which one ran and which were considered. **No
manager is ever installed.** If none is found the attempt stops with RC600 and
names all three; downloading a package manager would be a larger action than the
reproduction, and it would not be visible in the report.

`--network` is required. Conda resolves packages from channels by default, and
ReproCheck does not assume an offline solve is possible or safe. The environment
is created **inside the temporary workspace**, never inside or beside the
analysed project, and the original project is never written to.

> **A Conda environment is not a security boundary.** Creating one runs a
> third-party solver that downloads and executes packages; a `pip:` subsection
> inside the file runs a build backend; `--runtime-checks` imports the project's
> code. A virtual environment, Conda or otherwise, changes which packages are
> installed and nothing about authority. For code you do not trust, use a
> container or a VM. See [SECURITY.md](https://github.com/RafaelMoreiraDev/ReproCheck/blob/main/SECURITY.md).

| ID | Severity | Confidence | Rule |
| --- | --- | --- | --- |
| RC600 | error | high | No supported Conda-compatible manager is available |
| RC601 | error | high | Several environment files were found, or none, and nothing was chosen |
| RC602 | error | high | The environment could not be created |
| RC603 | error | high | The environment's own Python does not match what the file declares |
| RC604 | info | high | A Conda reproduction was requested without network permission |

A Conda attempt that creates the environment and finds it clean is `PARTIAL`,
never `PASS` on its own: the full test suite, external datasets and remote
services stay under **Not verified** exactly as they do for a pip run.

**No Conda finding is ever patched.** Choosing a Python version, a constraint, a
channel, or which of two installers wins is a decision ReproCheck will not make.

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
- Dependencies are read from `pyproject.toml`, `requirements*.txt` and Conda
  environment files. `setup.py`, `setup.cfg` and `Pipfile` are not parsed, and
  `constraints.txt` is only read when a `-c` include points to it.
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
- A comparison sees only what a report records. A finding that moved from one
  file to another is a resolution plus an addition, a dependency that changed
  group is a resolution plus an addition, and a finding whose message was
  reworded is an edit even when the underlying fact is identical.
- The identity of a finding uses its evidence text, so a reworded message that
  keeps the same evidence is an edit while a different evidence string is a
  different fact. That is a deliberate trade: stability over precision.
- A comparison cannot tell *why* something changed. Two reports of the same
  commit on two machines can differ, and the diff reports the difference without
  attributing a cause.
- The comparison requires both reports to describe the same project, proved by
  the normalised absolute path. A project moved to another directory cannot be
  compared with its own history, and there is no override.
- Older baselines are accepted and normalised by absence, so a schema `1`
  baseline compares only the findings. Nothing is migrated or guessed.
- Reports now go to a per-user state directory, which means two people do not
  share a baseline by default and a CI job needs `REPROCHECK_STATE_DIR` or an
  explicit path to keep one between runs.
- `suggest` covers nine findings and produces exactly one kind of patch. Every
  other finding is either declined with a stated reason or reported as
  informational; nothing is patched speculatively, which also means the tool
  cannot fix most of what it finds.
- A suggestion is generated from the state of the repository at scan time. It
  does not know whether a maintainer would accept the change, and `SAFE` says
  nothing about that.
- The RC140 patch appends patterns at the end of the file. It cannot insert them
  in a logical section, add a comment, or normalise patterns it did not add.
- `suggest` re-scans the project instead of reading a saved report, so
  `reprocheck suggest --report` does not exist in V0.9.
- `fix` writes to a project, so its guarantees are narrow on purpose: one named
  `SAFE` suggestion, one file, one write. There is no "apply all", no
  interactive prompt and no partial application.
- The precondition hash is checked over a window of milliseconds, because the
  suggestion is regenerated in the same run. `STALE` therefore only appears in a
  race with another writer — which is exactly the case it is there for, and also
  the reason it is hard to observe.
- The post-write validation is a static re-scan. It proves the finding is gone,
  not that the project behaves: nothing is installed, no test runs and no
  reproduction runs after a fix.
- The rollback restores bytes, not intent. It refuses to act when the file
  changed after the fix, which means a manual review is needed in that case.
- A `ROLLBACK_FAILED` leaves the file in an unknown state on purpose: the report
  and the backup path are the only way out, and hiding it would be worse.
- `setup.py` is a real build for some projects (astropy compiles C extensions
  through it) and is not parsed: its dependencies and its extension step are
  invisible.
- CI outside GitHub Actions is not inspected. `azure-pipelines.yml` and
  `.circleci/` are not read, so SHA pinning is not verified there.
- Every project in the external validation derives its version from Git
  metadata, so a copy without `.git` cannot be installed at all. This is the
  single most common reproducibility obstacle found so far.
- No dynamic step of the validation was isolated at the operating-system level:
  the build backends ran with the current user's privileges.
- Only `pyproject.toml`-based projects were validated. A `setup.py`-only or
  `setup.cfg`-based distribution is a blind spot none of the five exercises.
- A project whose **test suite contains path fixtures** gets RC120 and RC121
  findings about the fixtures themselves. ReproCheck reports them because they
  are true statements about the text of those files, and there is deliberately
  no exclusion for `tests/`: a test that opens a data file which is not in the
  repository is a real problem, and hiding the whole directory would hide it.
- No AI, no auto-fix and no scoring. The recommendations are static sentences
  attached to finding IDs, not advice derived from the project, the suggestions
  come from a fixed rule table, and the writes come from a named suggestion the
  user typed. The network is only used by an explicit `--network` during
  installation.

## Roadmap

- `0.11` - `setup.cfg`, `Pipfile` and Conda dependency sources, PEP 735
  `include-group` resolution, and non-Python ecosystems (Node).
- later - an explicit opt-in mode that preserves Git metadata in the
  reproduction copy, so a tag-derived version can be reproduced, plus entry
  point based import discovery.
- later - comparing more than two points in time, so a drift becomes a trend
  instead of a pair.
- later - `fix --verify`, which would run a reproduction after an application
  instead of a static re-scan, always opt-in and never by default.


