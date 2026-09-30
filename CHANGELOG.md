# Changelog

All notable changes to ReproCheck are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[PEP 440](https://peps.python.org/pep-0440/) versions.

## [Unreleased] — 2026-09-30

Validation of the Conda reproduction against a real solver. **No version bump,
no tag, no publication**: the published version is still `0.11.0b3`.

### Fixed

- **A Conda prefix on Windows is no longer assumed to be a virtual
  environment.** A real prefix keeps `python.exe` at the prefix root, so an
  environment that had been created successfully, with exit code 0, was
  reported as a failure because the interpreter appeared to be missing. The
  interpreter path is now probed, and a genuinely missing one still names the
  path that was expected. Found by running `tqdm` and `mne-python` against
  micromamba 2.9.0.
- **`pip check` can fail for a reason that is not a version mismatch.** pip
  exits non-zero for a distribution it refuses to install, reporting
  `wcwidth 0.9.1 is not supported on this platform`. No pattern recognised it,
  so a real environment defect was reported as "not clean" with nothing named
  and no finding at all. That shape is now parsed, and a failure still not
  recognised says so rather than claiming a conflict.
- **The Conda import smoke test says why it has nothing to do.** A
  `conda env create` does not install the project, so there is no module of the
  project's own to import. The reason now says that, instead of
  "distribution not installed", which read as a project that failed to install.

### Changed

- The optional integration marker is `real_conda`, and it now covers a real
  end-to-end solve and a real pytest collection, not only `--version`. Still
  never part of the default gate: `pytest -m real_conda` needs a manager, a
  network and minutes of wall clock.
- **The real-manager tests run on a schedule**, in a separate workflow
  (`real-conda.yml`) on Windows, weekly and by hand. It is deliberately not a
  required check and not triggered by a push or a pull request: a real solve
  takes tens of minutes, and putting that on the critical path of every commit
  is how a signal becomes noise. It caches the package and repodata cache and
  never the environment, so every run still solves.
- The fake manager in the test suite no longer installs the project into the
  prefix, and copies pytest's real dependency closure, walked from the
  distributions' own metadata, rather than a hand-written list.

### Not changed, deliberately

- Only micromamba was validated against a real binary. `conda` and `mamba` are
  still supported and still untested, and their `--json` shapes are assumed.
- Real Conda reproduction is not added to the main CI workflow. It needs a
  network and minutes per job, which is what makes it flaky rather than useful.

## [Unreleased] — 2026-09-29

Opt-in Conda environment reproduction. **No version bump, no tag, no
publication**: the published version is still `0.11.0b3`.

### Added

- **`reprocheck reproduce --conda` builds the project's Conda environment.**
  Opt-in and off by default. Without the flag the pipeline is the pip one it
  has always been; the two strategies are never merged, and a report records
  which one produced it, so a baseline comparison shows a strategy change as a
  strategy change rather than as a change of versions.
- **Three managers, in a fixed order: micromamba, mamba, conda.** Discovery
  stops at the first found and the report records the winner, its version and
  every candidate that was considered. **No manager is ever installed**: if none
  is found the attempt stops with RC600 and names all three. Downloading a
  package manager would be a larger action than the reproduction, and it would
  not be visible in the report.
- **`--network` is required on top of `--conda`.** Conda resolves from channels
  by default, and ReproCheck does not assume an offline solve is possible or
  safe. The gate is reported as RC604, informational, before any process starts.
- **Five checks, RC600 to RC604.** RC600 no manager, RC601 no environment file
  or several and none chosen, RC602 the environment could not be created, RC603
  the environment's own Python disagrees with what the file declares, RC604 the
  network gate.
- **`--conda-env` and `--conda-manager`** name the file and the manager instead
  of leaving either to a preference, because a reproduction that silently picks
  one of two environment files is not a reproduction.
- **The environment is created at `<workspace>/conda-env`**, outside the project
  and never at `./env` inside it, and it runs with `PYTHONPATH` cleared and
  `PYTHONNOUSERSITE` set. A prefix that can see the host's packages is not the
  environment its file describes.
- **The environment's own Python, `pip check` and its installed packages** are
  read for the report, so "the environment was created" and "the environment is
  clean" are two different claims.

### Changed

- A Conda attempt that creates the environment and finds it clean is `PARTIAL`,
  never `PASS` on its own. The full test suite, external datasets and remote
  services stay under *Not verified*, as they do for a pip run.
- `--runtime-checks` accepts an arbitrary interpreter, so the import smoke test
  and `pytest --collect-only` run against the Conda prefix rather than the host.

### Not changed, deliberately

- No Conda finding produces a patch. Choosing a Python version, a constraint, a
  channel, or which of two installers wins is a decision ReproCheck will not
  make.
- The default path is still pip. Conda reproduction is available; it is not the
  default.

### Verified

- 721 tests, 0 failures, 3 skipped; the 49 Conda tests use a fake manager and
  never contact a network, and a marker keeps any real integration out of the
  default gate.
- `tqdm` and `mne-python` were run against the real flag. **No Conda manager is
  installed on this machine and installing one was out of scope**, so the runs
  establish only that discovery refuses cleanly, starts no process, contacts no
  channel, leaves both projects byte for byte unchanged, and reports RC600. That
  a real environment is created and matches its file is **not** verified here.

## [Unreleased] — 2026-09-29

Static support for Conda environment declarations. **No version bump, no tag, no
publication**: this work is not released yet, and the published version is still
`0.11.0b3`.

### Added

- **Conda environment declarations are read.** `environment.yml` and
  `environment.yaml` at the root are parsed for their name, channels, variables,
  Conda dependencies, the `pip:` subsection and the Python pin. A file named
  `environment-<something>.yml` is read only when the project names it in the
  README, the docs or a workflow; no YAML is guessed to be an environment.
- **Six checks, RC230 to RC235.** RC230 and RC231 report a Python version and a
  dependency that the environment and the pip metadata prove incompatible.
  RC232, RC233 and RC235 are informational observations. RC234 reports a file
  that could not be read.
- **Conda version syntax is translated only where it is safe.** `numpy=1.26` is
  the 1.26 series, not `numpy==1.26`, and a build string such as
  `=1.26=py311np123` is not a version. The original text is kept verbatim, and a
  constraint with no exact PEP 440 equivalent is left uncompared.
- **Platform selectors are recorded, not ignored.** `# [win]` is a YAML comment
  and `safe_load` discards it, so selectors are recovered from the raw text and
  attached by line number. A dependency that applies to one platform is never
  compared as if it applied to all of them, which is what would create a
  conflict on no platform at all.
- **A `conda` section in the report**, in the JSON, the Markdown and the
  terminal summary, and a `conda` domain in the baseline comparison covering an
  environment added or removed, its name, its Python spec, its channels, and a
  dependency or requirement added, removed or re-specified. Channel order,
  comments and formatting are not compared.
- **A report states that the environment was not reproduced.** A project that
  declares a Conda environment gets it listed under *Not verified*: no conda
  process was run, no channel was contacted and no environment was solved.

### Changed

- **`PyYAML>=6.0.2` is a second runtime dependency**, used with `safe_load` only.
  Writing a YAML parser for Conda inside this project would be a worse outcome
  than the dependency, and 6.0.2 is the first release with wheels for every
  Python in the supported matrix.
- A Conda Python pin is classified as its own kind. It is deliberately not a
  project declaration, because RC103 compares project declarations against each
  other and would report the same disagreement twice under two ids.

### Not changed, deliberately

- No Conda finding produces a patch. Choosing a Python version, a constraint, a
  channel, or which of two installers wins is a project decision.
- No check has an opinion about Conda. Using `defaults`, using `conda-forge`,
  not pinning, having a `pip:` subsection and having no lockfile are all valid
  and produce nothing.
- Conda environments are not reproduced. `reproduce` still uses pip.

## [0.11.0b3] — 2026-09-28

The distribution is renamed. Nothing about how the tool behaves changes.

### Changed

- **The distribution is now `reprocheck-cli`.** The PyPI refuses to register
  `reprocheck` for similarity with an unrelated project that already owns
  `repro-check`, and the name was not worth fighting for. The product is still
  **ReproCheck**, the command is still **`reprocheck`**, the importable package is
  still **`reprocheck`**, and the repository is still
  [`RafaelMoreiraDev/ReproCheck`](https://github.com/RafaelMoreiraDev/ReproCheck).
  Only the name on the index changed, and the install command with it:

  ```bash
  pip install reprocheck-cli
  ```

  `reprocheck.__init__` exports `DISTRIBUTION_NAME` so the name lives in one
  place, and the Trusted Publisher on the PyPI side is registered for
  `reprocheck-cli`.

- Bumped past `0.11.0b2`, which was the version prepared for the release
  pipeline change and was never published. Keeping it would have published a
  version whose metadata name no longer matches the index.

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

[0.11.0b3]: https://github.com/RafaelMoreiraDev/ReproCheck/releases
[0.11.0b2]: https://github.com/RafaelMoreiraDev/ReproCheck/releases
[0.11.0b1]: https://github.com/RafaelMoreiraDev/ReproCheck/releases
