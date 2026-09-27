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

Excluded from the copy: `.git`, `.hg`, `.svn`, `.venv`, `venv`, `env`,
`__pycache__`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `.tox`, `.nox`,
`node_modules`, `build`, `dist`, `*.egg-info`, `*.pyc`, `*.lock` and files above
64 MB. Everything else is copied, because a reproduction that silently drops
source is worse than one that fails.

## What is NOT isolated

**A virtual environment is not a sandbox.** Running `pip install` on an
untrusted project executes code with the full privileges of the current user:

- the PEP 517 **build backend runs arbitrary Python** — `setup.py` is executed
  by setuptools, and an in-tree or third-party backend can do anything;
- `pyproject.toml` can point at a backend that is downloaded and executed;
- an sdist build compiles code and runs its own `setup.py`;
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
