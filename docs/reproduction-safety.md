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

The two reports are written to the paths chosen on the command line (by default
`./reprocheck-report.json` and `./reprocheck-report.md` in the current
directory) and nowhere else. Neither file can contain more than what the run
observed:

| Field | Source |
| --- | --- |
| `findings` | the deterministic checks, unchanged from V0.1 to V0.6 |
| `reproduction` | the steps that ran, the exit codes and the log paths |
| `verdict` | a derivation of the findings: `PASS`, `PARTIAL`, `FAIL` or `NOT_ATTEMPTED`, with the reasons |
| `verdict.not_verified` | what was never checked, with the reason; never a failure |
| Markdown report | the same content, arranged for a reader; no conclusion of its own |

The verdict cannot turn a `scan` into an execution: `NOT_ATTEMPTED` is the only
verdict a `scan` can produce, whatever the findings are. It is a label, not a
measurement, and it never becomes a score. `PASS` means "the steps that ran
succeeded", so the `Not verified` list is part of every report: a reader must
see, next to the verdict, that the full test suite, the external data and the
CI revisions were never checked.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | the command completed; for `reproduce`, the verdict is `PASS` |
| `1` | the verdict is `PARTIAL` |
| `2` | the verdict is `FAIL` |
| `3` | operational error: the path is unusable or a report could not be written |

Code `2` is also argparse's own code for a malformed command line, which is why
operational errors use `3`. A failed attempt keeps its workspace and therefore
its logs, exactly as before; nothing about the reproduction behaviour changed to
produce a verdict.
