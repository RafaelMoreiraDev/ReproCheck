# Reproduction safety

`reprocheck reproduce` is the first ReproCheck command that **executes code
from the analysed project**. Everything below states exactly what is isolated,
what is not, and why a virtual environment is not a security boundary.

## What is isolated

| Aspect | Guarantee |
| --- | --- |
| The analysed project | Only ever **read**. It is copied into the workspace and all work happens on the copy |
| Virtual environment | Created at `<workspace>/venv`, outside the project, never at `.venv` inside it |
| Git | The copy excludes `.git`, so no `checkout`, `reset`, `clean`, hook or commit can run. Git is only queried read-only for the fingerprint |
| Lockfiles and configuration | Written only inside the workspace copy, if the project's own build writes them |
| Conda environment | Created at `<workspace>/conda-env`, outside the project, never at `./env` inside it. Only with `--conda` |
| Logs and report | Live under `<workspace>/logs` and the report destination chosen by the user |
| Integrity | Size and mtime of every file, plus `git rev-parse HEAD` and `git status --porcelain`, are captured before and after. Any difference is reported as **RC406**, an error severity finding about ReproCheck itself |
| README | Never executed. It stays evidence, never code |
| Test execution | Never. Only `pytest --collect-only` runs, and only with `--runtime-checks` |

Excluded from the copy: `.git`, `.hg`, `.svn`, `.venv`, `venv`, `env`,
`__pycache__`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `.tox`, `.nox`,
`node_modules`, `build`, `dist`, `*.egg-info`, `*.pyc`, `*.lock` and files above
64 MB. Everything else is copied, because a reproduction that silently drops
source is worse than one that fails.

## What is NOT isolated

## What each step executes

`reprocheck reproduce` runs, in this order: the analysis (read-only), then the
installation. With `--runtime-checks` it runs two more steps, and both execute
project code:

### Conda environment creation (`--conda` only)

`conda env create --prefix <workspace>/conda-env --file environment.yml` runs a
third-party **solver**, and a solver that resolves a package from a channel will
download it, unpack it and run its install scripts. This is the single most
consequential step in the whole product, and it is why `--conda` is opt-in, why
`--network` is required on top of it, and why no manager is ever installed to
make it work.

Three further points apply only to the Conda path:

- A **`pip:` subsection inside the environment file** runs a PEP 517 build
  backend for every source distribution it names, as a side effect of the solve.
- **`--runtime-checks` imports the project's code from inside the environment**,
  which is a different set of installed packages from the venv the pip path
  would have used, and therefore a different set of import-time side effects.
- The environment runs with **`PYTHONPATH` cleared and `PYTHONNOUSERSITE` set**,
  so it cannot import from the host. That is a correctness requirement rather
  than a hardening measure: a prefix that can see the host's packages is not the
  environment its file describes.

### Installation

`pip install <workspace>/source` runs the declared **PEP 517 build backend**.
For a setuptools project that means executing `setup.py`; a third-party or
in-tree backend can run any code it likes, and an sdist build may compile
sources. This happens on every `reproduce`, with or without `--runtime-checks`.

### Import smoke test (`--runtime-checks` only)

`python -c "import <module>"` runs the installed package's `__init__.py` and
anything it imports. A package that does work at import time — downloading
data, opening sockets, reading the home directory, spawning processes — will do
it here. Each import is a separate process with a 30 second timeout, and the
whole process tree is killed when the timeout expires.

### Pytest collection (`--runtime-checks` only)

`python -m pytest --collect-only -q` runs:

- `conftest.py` files, in full;
- installed pytest plugins and their hooks;
- the import of every test module;
- module-level code in those test modules.

Test functions are **not** executed, and no test is ever run by ReproCheck in
V0.5 or V0.6. Collection is not a passive operation.

### Consequence

`--runtime-checks` should only be used on code you trust as much as code you
would import, or inside a disposable container/VM. It is not a sandbox, and
neither is the virtual environment.

**A virtual environment is not a sandbox.** Running `pip install` on an
untrusted project executes code with the full privileges of the current user:

- the PEP 517 **build backend runs arbitrary Python** — `setup.py` is executed
  by setuptools, and an in-tree or third-party backend can do anything;
- `pyproject.toml` can point at a backend that is downloaded and executed;
- an sdist build compiles code and runs its own `setup.py`;
- `--runtime-checks` adds module imports and pytest collection on top;
- the installed code is then importable with the user's file access, network
  access and environment variables.

A venv isolates *package versions*, not *authority*. Nothing in ReproCheck
prevents a malicious project from reading `~/.ssh`, writing to the user's
directories or opening sockets.

**Therefore:**

- do not run `reprocheck reproduce` on a project you would not run `pip install`
  on directly;
- for untrusted code, use an operating-system or container-level sandbox (a
  disposable VM, a container without host mounts, a network namespace with no
  egress) and run ReproCheck inside it;
- `--network` is off by default, which is a *reproducibility* control, not a
  security control. It does not stop a build backend from making network calls
  of its own;
- the workspace path and the log files contain whatever the build printed.
  Treat them as untrusted output.

## Network policy

| Mode | Behaviour |
| --- | --- |
| default | `pip` runs with `--no-index` and `PIP_NO_INDEX=1`. Only what is already available locally can be installed |
| `--network` | The index is reachable **for the installation step only**. No other request is made by ReproCheck, and the analysis stays offline |

`--runtime-checks` does not open the network by itself. An imported module or a
pytest plugin may still do it, which is exactly why the flag is opt-in.

When an offline installation fails because the dependencies were not available
locally, ReproCheck reports **RC404** — but only when the pip output proves it.
The conclusion is drawn from the error text alone; no request is made to
confirm it.

## Python selection

Only interpreters already installed on the machine are used; ReproCheck never
installs Python. The choice is deterministic and refused when it is not:

1. `.python-version` wins, but only when it agrees with the project requirement;
2. otherwise the project requirement decides, and the **lowest installed
   version that satisfies it** is chosen;
3. otherwise a single version declared by CI is used;
4. otherwise, with no declaration and several interpreters installed, nothing is
   chosen and the reproduction stops with **RC405**.

## Workspace cleanup policy

| Outcome | Workspace |
| --- | --- |
| Successful attempt (install succeeded and `pip check` clean) | Deleted |
| Successful attempt with `--keep-workspace` | Kept |
| Any failure | Kept automatically, because that is where the evidence lives |

`report.reproduction.workspace` is set to `null` when the workspace was removed,
so a report never points at a directory that no longer exists.

## What the reports contain

The reports are written to the paths chosen on the command line, or to the
per-user state directory when none is given (see "Where files are written" in
`README.md`), and nowhere else. Nothing is ever written inside the analysed
project. Neither file can contain more than what the run observed:

| Field | Source |
| --- | --- |
| `findings` | the deterministic checks, unchanged from V0.1 to V0.6 |
| `reproduction` | the steps that ran, the exit codes and the log paths |
| `verdict` | a derivation of the findings: `PASS`, `PARTIAL`, `FAIL` or `NOT_ATTEMPTED`, with the reasons |
| `verdict.not_verified` | what was never checked, with the reason; never a failure |
| Markdown report | The same content, arranged for a reader; no conclusion of its own |
| baseline / diff | Two report documents compared; no project is opened and no step is re-run |

The verdict cannot turn a `scan` into an execution: `NOT_ATTEMPTED` is the only
verdict a `scan` can produce, whatever the findings are. It is a label, not a
measurement, and it never becomes a score. `PASS` means "the steps that ran
succeeded", so the `Not verified` list is part of every report: a reader must
see, next to the verdict, that the full test suite, the external data and the
CI revisions were never checked.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | the command completed; for `reproduce`, the verdict is `PASS`; for `baseline compare`, the comparison completed **whether or not anything changed** |
| `1` | the verdict is `PARTIAL` |
| `2` | the verdict is `FAIL` |
| `3` | operational error: the path is unusable, a report could not be written, or a baseline was missing, invalid, in an unknown schema, or already present |

Code `2` is also argparse's own code for a malformed command line, which is why
operational errors use `3`. A failed attempt keeps its workspace and therefore
its logs, exactly as before; nothing about the reproduction behaviour changed to
produce a verdict.

## What `suggest` does and does not do

`reprocheck suggest` is a static scan followed by a rule table. Like `scan`, it
is read-only by construction:

- it never opens a file of the analysed project for writing, never renames and
  never creates one, and it never creates a copy of the file next to the project;
- a patch is built as two strings in memory -- the current content and the
  proposed content -- and rendered as a unified diff;
- it never applies anything: there is no `--apply`, no `reprocheck fix` and no
  interactive prompt;
- it runs no project code, resolves no dependency and makes no network request;
- the fingerprint of the project, its Git HEAD and `git status --porcelain` are
  unchanged by a run; the test suite asserts this by hashing every file before
  and after.

The one place where a patch would need care is a file whose bytes cannot be
represented as text. A `.gitignore` with a byte-order mark or with non-UTF-8
content gets **no** patch: the suggestion is downgraded to `REVIEW_REQUIRED`
with the reason stated, because a `before` that does not match the file on disk
produces a diff nobody can review.

## What `fix` does and does not do

`reprocheck fix` is the only command that writes to the analysed project, and
every part of that is deliberate:

- **One suggestion, named by the user.** The identifier is required. There is no
  "apply all", no prompt, and no way to name a `REVIEW_REQUIRED` or `MANUAL_ONLY`
  suggestion: those are refused before anything is read for writing.
- **A dry run is the default.** Without `--apply`, the command prints the
  precondition hashes, the diff and the fact that nothing was written.
- **The suggestion is regenerated first.** A `reprocheck-suggestions.json` file
  is never trusted; the checks run again and the proposal is built from the
  project as it is at that moment.
- **The write is atomic.** The new bytes go to a temporary file in the target's
  own directory, are flushed, `fsync`ed when supported, and replace the original
  with `os.replace`. The original file mode is preserved, and the temporary file
  is removed if anything fails — so a crash cannot leave a half-written
  `.gitignore` behind, and no `.bak` or temp file ever appears in the project.
- **The bytes are the proposed bytes.** No formatter is involved and no file is
  reconstructed; the content written is exactly what the rule produced, with the
  encoding, the line endings, the existing lines and the presence or absence of
  a final newline preserved.
- **A precondition hash guards the window.** The SHA-256 of the target's exact
  bytes is compared with the one the rule recorded. A mismatch means another
  writer acted in between: ReproCheck does not merge and does not overwrite.
- **The backup lives outside the project**, in ReproCheck's state directory, and
  the operation is recorded in an auditable JSON file there.
- **The result is verified.** The file is read back and hashed, and the project
  is re-scanned statically; the target finding must be gone. Any failure restores
  the backup and verifies the restoration. `reproduce` is never run: that would
  install and execute project code, and it is a separate, explicit command.
- **Git is only observed.** HEAD and `git status --porcelain` are read before and
  after. No commit, no branch, no `checkout`, no `reset`, no `clean`. If any path
  other than the target changed during the operation, the report says so and
  ReproCheck leaves those files alone.
- **The rollback refuses to overwrite later work.** `fix --rollback` restores the
  backup only while the file still matches the hash the application recorded.

The residual risk is stated plainly: after `APPLIED`, the change is one
intentional edit to one file. Every other finding, and every other file, is
exactly as it was, and ReproCheck says nothing more than that.

## What a baseline comparison does and does not do

`reprocheck baseline` is the only command family that reads two files instead of
analysing a directory, and it is the least invasive:

- it **never** opens the analysed project: both sides are report documents;
- it **never** re-runs a check, an installation, an import or a collection, so
  enabling `--runtime-checks` is not needed to compare two reports and no project
  code is executed by a comparison;
- it **never** writes to a baseline unless `save` was asked to, and `save`
  refuses to replace an existing one without `--force`;
- it **refuses** to compare two different projects, proved by the normalised
  absolute path of the analysed directory. A moved project cannot be compared
  with its own history, and there is no override in V0.9;
- it makes no network request and installs nothing.

The comparison is therefore safe to run on a report from an untrusted source, in
the sense that the input is treated as data: an invalid, truncated or
foreign document is refused with an operational error instead of being
interpreted. Two limits remain: a report can contain text copied from another
project, and a very large file is read into memory, so a hostile report is a
resource question, not a correctness one.
