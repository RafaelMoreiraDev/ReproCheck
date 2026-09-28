# Changelog

All notable changes to ReproCheck are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[PEP 440](https://peps.python.org/pep-0440/) versions.

## [0.11.0b2] — 2026-09-28

The first release published to the real PyPI. No functional change from
`0.11.0b1`; the pipeline is what changed.

### Added

- `SECURITY.md` and `SUPPORT.md`.

### Changed

- **Publishing moved from a manual API token to GitHub OIDC.** Releases are built
  and uploaded by `.github/workflows/release.yml` using Trusted Publishing, which
  is what `0.11.0b1` could not use. No long-lived PyPI secret is stored in the
  repository any more, and the credential a release uses is short-lived and
  issued per run.
- The release workflow refuses to publish when the tag and the package version
  disagree, so a mis-tagged commit cannot upload mismatched artifacts.

## [0.11.0b1] — 2026-09-28

The first release candidate. Nothing has been published: no PyPI upload, no
GitHub release, no tag, no remote.

### Added

- **Static project audit** — read-only. README, Python declarations, tests,
  `.gitignore`, Git state, build configuration.
- **Consistency rules** — conflicting Python requirements across `.python-version`,
  `pyproject.toml` and CI; lockfile and package-manager coherence; missing files
  the README documents; version-constraint conflicts proven with a witness
  version.
- **Dependency audit** — `pyproject.toml` and `requirements*.txt`, including
  `-r`/`-c` includes, with every duplicate, unbounded declaration, external URL
  and mutable VCS reference reported.
- **CI reference audit** — GitHub Actions and reusable workflows, with mutable
  refs, inconsistent versions of the same action and missing local actions.
- **Version provenance** — a static warning when the distribution version comes
  from Git metadata, which a copy without `.git` cannot reproduce.
- **Isolated reproduction** — a copy of the project in a temporary workspace, a
  selected interpreter, a virtual environment outside the project, an
  installation, `pip check`, the installed version, and a before/after
  fingerprint of the original project.
- **Optional runtime checks** (`--runtime-checks`) — import smoke test derived
  from the installed distribution, and `pytest --collect-only` when pytest is
  already present. Never executes a test function.
- **Verdict** — `PASS`, `PARTIAL`, `FAIL` or `NOT_ATTEMPTED`, derived by
  explicit rules, with an explicit list of what was **not verified**.
- **Human reports** — a Markdown report beside the JSON one, evidence-first and
  with empty sections omitted.
- **Baselines and comparison** — save a report as a reference and compare a
  later one, reporting what changed factually and refusing to compare two
  different projects.
- **Deterministic fix suggestions** — nine findings are answered, and only one
  (`RC140`, missing `.gitignore` patterns) produces a patch. No model, no
  inference, no network.
- **Safe application** (`fix --apply`) — one named suggestion, a precondition
  hash, an atomic write, a backup outside the project, an audit record, a
  static re-scan as a post-condition, and a rollback that refuses to overwrite
  later work.
- **External validation** — five public repositories scanned, suggested and
  partly reproduced, with every warning reviewed by hand; see
  `docs/external-validation.md`.

### Fixed during validation

- Six classes of false positive in the path and README checks, each with a
  regression test: a path constructor read as a file read, a call name matched as
  the tail of another identifier, commented-out code read as a reference, a
  documented install from a URL read as a missing file, a project API named
  `open` or a file being written read as a read, and a drive prefix followed by
  an escape sequence read as a Windows path.
- A `SAFE` patch that was lossy on CRLF files: the carriage return is now part
  of the diff body, so `git apply` reproduces the proposed bytes exactly.
- A unified diff whose hunk header disagreed with its own body after the
  no-final-newline transform, which made the patch *corrupt* for `git apply`.
- `fix --rollback <record> --apply` was accepted and `--apply` ignored; the
  combination is now refused.

### Known limitations

- `environment.yml` and other Conda sources are not parsed. A committed Conda
  environment, with its Python version and full dependency list, is invisible.
- CI outside GitHub Actions is not inspected: `azure-pipelines.yml` and
  `.circleci/` are not read, so SHA pinning is not verified there.
- `setup.py` and `setup.cfg` are not parsed. A project whose build compiles
  native code through `setup.py` shows no finding about it.
- A project whose version is derived from Git tags **cannot be installed** from
  a reproduction copy, because the copy has no `.git`. This is the single most
  common obstacle found in the external validation, and it is the project's own
  release process rather than a ReproCheck failure.
- A virtual environment isolates versions, not authority. `reproduce` runs the
  analysed project's build backend, and `--runtime-checks` imports its code, with
  the current user's privileges. Untrusted code belongs in a container or a VM.
- `PASS` means only that the checks which ran succeeded. Items listed under
  **Not verified** — the full test suite, external datasets, remote URLs, CI
  action revisions, index state — remain unknown.
- Dependency sources are limited to `pyproject.toml` and `requirements*.txt`,
  and workflow parsing is line-based: YAML anchors, multi-line values and
  `env`-based indirection are not resolved.
- Only one kind of fix can be applied (`RC140`), and only one suggestion at a
  time. There is no "apply all" and no interactive prompt.

[0.11.0b2]: https://github.com/RafaelMoreiraDev/ReproCheck/releases
[0.11.0b1]: https://github.com/RafaelMoreiraDev/ReproCheck/releases
