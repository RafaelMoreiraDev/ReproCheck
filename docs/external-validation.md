# External validation — real-world Python projects

TASK-011. The question this document answers is narrow and factual:

> Does ReproCheck behave reasonably outside the single project it was developed
> against?

Five public repositories were cloned, scanned, suggested and (in part) reproduced.
No rule in `src/reprocheck/` knows any of these names; no path of these
repositories appears anywhere in the code, and every fix below is a **generic**
correction with a regression test written from a synthetic fixture.

The clones live **outside** this repository, in `C:\Projetos\ReproCheckBenchmarks`,
and are never added to this Git repository. They are used as evidence and are
never modified: `git status --porcelain` was 0 lines before and after every run.

## Method

- shallow clones (`--depth 1`), so the identity below is exactly what was
  analysed;
- `reprocheck scan <repo>` first, on all five, with the reports written to a
  temporary state directory outside the clones;
- `reprocheck suggest <repo>` on all five, audited by hand;
- `reprocheck reproduce <repo>` without network on four, then with `--network`,
  then with `--network --runtime-checks` only where the installation succeeded;
- no pytest execution anywhere, no test collection beyond what `reproduce` does,
  no datasets, no model downloads.

**Isolation.** Docker is not installed on this machine, so there was **no
operating-system isolation** for the dynamic steps. `reproduce` executes the
build backend of the analysed project with the current user's privileges inside a
workspace copy; that is a real limitation of this validation and is recorded
below. The projects analysed are public, widely installed code, and the
dynamic steps were run only on those.

## Identity

| Project | URL | Commit | Branch | Cloned | Python files | Manifests found |
| --- | --- | --- | --- | --- | --- | --- |
| pint | https://github.com/hgrecco/pint | `e4042bbe5c66` | master | 2026-09-28 | 110 | `pyproject.toml` |
| tqdm | https://github.com/tqdm/tqdm | `9cf5a12b1f95` | master | 2026-09-28 | 68 | `pyproject.toml`, `environment.yml`, `Makefile` |
| astropy | https://github.com/astropy/astropy | `592070633f16` | main | 2026-09-28 | 1006 | `pyproject.toml`, `setup.py`, `tox.ini` |
| mne-python | https://github.com/mne-tools/mne-python | `47d5be239f12` | main | 2026-09-28 | 929 | `pyproject.toml`, `environment.yml`, `azure-pipelines.yml`, `Makefile` |
| napari | https://github.com/napari/napari | `4b1f6dd779c6` | main | 2026-09-28 | 1029 | `pyproject.toml`, `tox.ini`, `Makefile` |

They were chosen for structural diversity, not for a score: two pure-Python
libraries, one C-extension scientific package, one scientific package with
`azure-pipelines.yml` and a committed Conda environment, and one desktop
application with 101 CI references. The file counts are
`git ls-files`-independent approximations from the walk; the Python file count
comes from ReproCheck's own `project.python_file_count`.

## Results

| Project | Commit | Scan | Suggest | Reproduce | Runtime | ReproCheck crash? |
| --- | --- | --- | --- | --- | --- | --- |
| pint | `e4042bbe5c66` | 0.53 s, 0e/12w/6i | 1 SAFE, 7 review, 2 manual | offline FAIL (RC401+RC404), network FAIL (RC401) | ✅ on a temporary copy | no |
| tqdm | `9cf5a12b1f95` | 0.36 s, 0e/11w/11i | 1 SAFE, 9 review, 2 manual | offline FAIL (RC401+RC404), network FAIL (RC401) | not attempted | no |
| astropy | `592070633f16` | 3.80 s, 0e/9w/16i | 1 SAFE, 0 review, 4 manual | offline FAIL (RC401+RC404), network FAIL (RC401) | not attempted | no |
| mne-python | `47d5be239f12` | 11.06 s, 0e/38w/27i | 1 SAFE, 26 review, 3 manual | offline FAIL (RC401+RC404), network FAIL (RC401) | not attempted | no |
| napari | `4b1f6dd779c6` | 2.20 s, 0e/8w/66i | 1 SAFE, 3 review, 2 manual | network **PARTIAL**, install 439 s, `pip check` clean, `0.7.1.dev0` | ✅ `napari` and `napari_builtins` imported | no |

**Five scans, five suggests, eleven reproduce attempts, zero crashes, zero
tracebacks, zero unexpected writes.** Every clone ended the whole run with 0
dirty lines and an unchanged HEAD.

## The dominant finding: every project derives its version from Git

All five declare a dynamic version from VCS metadata, and RC150 fired on all
five. That is not a theoretical limitation: it is what the reproduction actually
hit. Offline, every install fails with `hatchling`/`setuptools-scm` not being
available locally (RC401 + RC404, with pip's own words in the evidence).
**With `--network`, four of the five still fail**, and always for the same
reason, quoting the backend:

```
SETUPTOOLS_SCM_PRETEND_VERSION ... No distribution name is known here
```

That is RC150's claim confirmed by execution rather than by reading a config: a
copy without `.git` cannot derive a tag version. The fifth, napari, installs
because its configuration provides a fallback version, and it reports
`0.7.1.dev0` — a *dev* version, which is itself a reproducibility fact a reader
should not have to dig for.

To confirm that the rest of the pipeline works on a VCS-versioned project, the
same reproduction was run on a **temporary copy of pint** with the backend's own
documented escape hatch in the operator's environment
(`SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0`). Result: install 23 s, `pip check`
clean, `import pint` OK, verdict `PARTIAL`, original clone untouched. No
ReproCheck feature is involved — the reproduction forwards the operator's
environment, and the knob belongs to setuptools-scm.

## Findings review

Every warning and error was reviewed by reading the cited source. The counts
below are per project, with a manual classification of the evidence.

| Project | Errors | Warnings | Info | Rules fired | Findings reviewed by hand |
| --- | --- | --- | --- | --- | --- |
| pint | 0 | 12 | 6 | 8 | 12 warnings |
| tqdm | 0 | 11 | 11 | 6 | 11 warnings |
| astropy | 0 | 9 | 16 | 8 | 9 warnings |
| mne-python | 0 | 38 | 27 | 11 | 38 warnings |
| napari | 0 | 8 | 66 | 10 | 8 warnings |

### Confirmed (the finding is right, and it matters)

| Project | Finding | Evidence | Note |
| --- | --- | --- | --- |
| pint | RC150 | `dynamic = ["version"]`, hatch-vcs | confirmed by the failed install |
| pint | RC220 ×7, RC221 ×2 | `actions/checkout@v4` and `@v6.0.2` in the same repo | the two-ref case is real |
| pint | RC116 | `[tool.uv]` with no `uv.lock` | the repository pins nothing |
| tqdm | RC150, RC220 ×9 | setuptools-scm; nine mutable action refs | confirmed |
| tqdm | RC140 | `.gitignore` has none of the six required patterns | confirmed by reading the file |
| astropy | RC140 | `.ruff_cache/` missing while ruff is configured | confirmed |
| astropy | RC116 | `[tool.uv]`, no lockfile | confirmed |
| mne-python | RC220 ×26 | every external action is on a tag or branch | confirmed; none is SHA-pinned |
| mne-python | RC116 | uv configured, no lockfile | confirmed |
| napari | RC140 | `.ruff_cache/` missing | confirmed |
| napari | RC220 ×3 | first-party workflows on `@main` | confirmed |

### Reasonable warnings (true statement, low value in that context)

| Project | Finding | Why it is only "reasonable" |
| --- | --- | --- |
| astropy | RC120 `/home/circleci/astropy-figure-tests/figures/$TOXENV` | a path inside a CircleCI container; intentional and portable across CircleCI runs |
| mne-python | RC120 ×6 in `azure-pipelines.yml` | CI agent home directories; the variable is expanded by the agent |
| mne-python | RC120 `/home/xyz/me2/` in `mne/datasets/utils.py` | a docstring example |
| astropy | RC120 `/Users/westfall/...` in a doctest | a traceback copied into documentation |
| astropy | RC120 `C:\\path\\file.docx`, `c:/path/to/the%20file.txt` | deliberate Windows-path test data |
| napari | RC120 `/Users/.../settings.yaml` in an issue template | a placeholder for the reporter to fill |
| astropy | RC121 `load('de421.bsp')` | a file the test suite downloads; the finding is true and the message says so |

### False positives found and fixed in this task

All five classes were reproduced on real code, then fixed generically. Each fix
has a regression test built from a synthetic fixture — no third-party file was
copied into this repository.

| Class | Real example | Root cause | Fix |
| --- | --- | --- | --- |
| `Path("x")` treated as a file read | `pint/testsuite/test_dask.py:140` (`mydask.png` is created and unlinked by the test), `astropy/.../test_covariance.py:652` (`test_covar_io.fits` is written), `napari/.../test_add_layers.py:89` | the call list contained the `Path` *constructor* | `Path(...)` is no longer a reading call; `open`, `read_csv`, `np.load`, `io.open` still are |
| call name matched as the tail of another identifier | `astropy/io/ascii/tests/test_ecsv.py:1005` (`yaml.safe_load("\n".join(...))` reported as `load('\n')`), `pint/testsuite/test_unit.py:352` (`joinpath("default_en.txt")` reported as `path(...)`) | the regex had no left word boundary | `(?<![A-Za-z0-9_])` before the call name; a dot is still allowed so `np.load` and `io.open` keep working |
| commented-out code read as a reference | `mne-python/mne/_fiff/tests/test_reference.py:403` (`# load('leadfield.mat', 'G')`, a MATLAB snippet in a comment) | comments were scanned | a line whose first non-space characters are `#` or `//` is skipped |
| a documented install from a URL read as a missing file | `mne-python/README.rst:58` (`pip install --upgrade https://…/main.zip` reported as a requirements file) | `_is_safe_value` did not reject URLs | a value containing `://` is not a repository file |
| a project API named `open`, and a file being written | `napari/.../test_add_layers.py:89` (`viewer.open('mock_path.tif')`), `astropy/.../generate_spectralcoord_ref.py:60` (`open("rv.lis", "w")`) | any `open(` counted as a read | only the builtin and `io.open` read; an explicit `w`/`a`/`x`/`+` mode is a write |

Two more classes were found in the same pass and fixed:

| Class | Real example | Fix |
| --- | --- | --- |
| a drive prefix followed by an escape sequence | `mne-python/mne/minimum_norm/tests/test_inverse.py:137` (`didn't:\n{key}` read as `t:\n{k}`), `napari/tools/create_pr_or_update_existing_one.py:107` (`%s:\n\nstdout` read as `s:\n\nstdout`) | the first path component after `C:` must be at least two characters, and a value containing `{}` is a template, not a path |
| a CRLF file patched with an LF patch | `pint`, `tqdm`, `astropy`, `mne-python` all have CRLF `.gitignore` | the carriage return is now written back into the diff body, as `git diff` does with `core.autocrlf` disabled |

Warnings dropped from **103 to 78** across the five projects (astropy 24→9,
mne-python 45→38, napari 18→8, pint 14→12) with **no severity change anywhere**
and no rule made weaker to suit a project.

### Uncertain

| Project | Finding | Why it stays uncertain |
| --- | --- | --- |
| astropy | RC120 `/Users/westfall/...` inside a `>>>` doctest | ReproCheck cannot know the line is documentation; a reader can. Reported, not silenced |
| napari | RC115 (`conda activate napari-env` in the README) | the environment name is not a package; the finding says so and stays `medium` |

## Safe suggestions: manual audit

Exactly **5** `SAFE` suggestions were produced, one per project, and all five are
the same `FIX-RC140-001` `.gitignore` patch. Each one was checked by hand
against the repository bytes:

| Check | Result |
| --- | --- |
| the change is additive | 5/5 — no line removed, the original content is a byte prefix of the proposal |
| existing content preserved | 5/5 — `before` equals the bytes on disk in all five |
| no invented value | 5/5 — the added lines are exactly the patterns RC140 proved missing, and nothing else |
| `git apply --check` accepts the patch | 5/5 |
| applying the patch with `git apply` reproduces the proposed bytes exactly | 5/5, after the CRLF fix (see below) |

The patterns per project: pint `*.egg-info/ .pytest_cache/ .ruff_cache/ .venv/`;
tqdm `*.egg-info/ .pytest_cache/ .venv/ build/ dist/ venv/`; astropy
`.ruff_cache/`; mne-python `*.egg-info/`; napari `.ruff_cache/`. In no case was
a pattern already present proposed again, and in no case was a line reordered.

**A `SAFE` suggestion was wrong before this task's fix.** The first audit round
showed `git apply` accepting the patch and then producing a file *different* from
the proposal for the four CRLF repositories, because the diff body carried no
carriage return. `git apply --check` cannot catch that: the patch is valid, it
is simply lossy. The fix (CR in the diff body) and its regression test
(`test_git_apply_reproduces_the_proposed_bytes`, three newline styles) live in
`tests/test_fix.py`.

`reprocheck fix` was **not** run with `--apply` on any benchmark clone. The
`--apply` path is covered by the internal suite and by the TASK-010 Open Climate
Fix copy; a temporary copy is where a real application belongs.

## Known gaps (false negatives, deliberately not fixed here)

| Gap | Evidence on these projects | Consequence |
| --- | --- | --- |
| `environment.yml` (Conda) is not parsed | `tqdm/environment.yml` and `mne-python/environment.yml` declare `python >=3.8` / `>=3.11` plus ~60 dependencies each | a whole declared environment is invisible to RC115/RC116/RC203/RC205 and to the Python consistency rules |
| CI outside GitHub Actions is not inspected | `mne-python/azure-pipelines.yml`, `astropy/.circleci/`, `napari/.circleci/` | SHA pinning is not verified there; an entire CI system is invisible to RC220–RC223 |
| `setup.py` is not parsed | `astropy/setup.py` builds C extensions through `extension_helpers` | the fact that the build compiles native code, and any dependency it declares, is not a finding |
| `doc/` requirements are not a distinct source | `mne-python/doc/` has 14 files, none of them `requirements.txt` | nothing missing here, but the pattern is unhandled |
| a build backend's own version fallback is not a finding | `napari` installs `0.7.1.dev0` from a copy without `.git` | the installed version is a dev version; the report states it, but no check says "this is not a release version" |
| a `dev`-suffixed version is not distinguished | idem | same |

## Performance

| Project | Python files | Text files scanned | Text read | `scan` |
| --- | --- | --- | --- | --- |
| pint | 110 | — | — | 0.53 s |
| tqdm | 68 | — | — | 0.36 s |
| napari | 1029 | — | — | 2.20 s |
| astropy | 1006 | — | — | 3.80 s |
| mne-python | 929 | 8608 | 20.0 MB | 11.06 s |

mne-python is the slowest because the path scanner reads **20 MB of text across
8608 files** (its test data is mostly `.json`). The profile was measured per
scanner (`paths` 6.94 s, `git` 0.31 s, everything else ≤0.01 s) and then per
stage inside the path scanner: walk+decode 1.48 s, regex 1.09 s, both growing
**linearly** with the file count (sampled every 100 files: 0.02 s → 1.48 s over
8608 files). There is no accidental quadratic behaviour; the cost is the size of
the repository. `suggest` costs the same as `scan` plus the rule table, and the
checks themselves are 0.00–0.02 s everywhere.

No repository produced "warning spam" in a new way: the most repeated rule is
RC220, and its findings are one per distinct target+ref with every location
listed, which is the intended shape. napari's 66 `info` findings are mostly one
RC202 per duplicated dependency, which is a fact per pair, not a repetition of
the same sentence.

## Regression tests added because of real findings

Every one of them is a minimal synthetic fixture; no third-party content is in
this repository.

| Test | Problem it locks down |
| --- | --- |
| `test_a_path_constructor_is_not_a_read` | `Path("out.png").unlink()` reported as a missing input file |
| `test_the_call_name_must_not_be_the_tail_of_an_identifier` | `yaml.safe_load(...)` and `Path.joinpath(...)` reported as reads |
| `test_commented_out_code_is_not_a_reference` | commented-out code reported as a reference |
| `test_a_dotted_reading_call_is_still_inspected` | `np.load(...)` must keep working after the boundary fix |
| `test_a_drive_prefix_alone_is_not_a_path` | `didn't:\n{key}` read as a Windows drive |
| `test_a_real_drive_path_is_still_reported` | a genuine `C:/Users/me/data.csv` is still reported |
| `test_a_file_being_written_is_not_a_read` | `open("generated.input", "w")` reported as a read |
| `test_a_project_api_named_open_is_not_a_read` | `viewer.open('mock_path.tif')` reported as a read |
| `test_io_open_is_still_a_read` | `io.open("absent.json")` is still a read |
| `test_an_install_from_a_url_is_not_a_missing_file` | `pip install https://…zip` reported as a missing requirements file |
| `test_git_apply_reproduces_the_proposed_bytes` | a CRLF file patched with an LF patch (3 newline styles) |
| `test_cli_rejects_apply_with_rollback` | `--apply` silently accepted with `--rollback` (CLI bug from TASK-010) |

## Validation metrics

- Projects cloned and validated: **5**
- Projects with a clean `scan` (exit 0, report written): **5**
- ReproCheck crashes, tracebacks or unhandled exceptions: **0**
- Unexpected modifications in any clone: **0** (0 dirty lines, unchanged HEAD, before and after)
- Findings reviewed by hand against the cited source: **78 warnings** (100% of them; there were no errors)
- False positives confirmed and fixed: **6 classes** (14 individual findings)
- `SAFE` suggestions reviewed: **5 of 5** (100%); incorrect ones found: **1 class** (CRLF patch), fixed
- Reproductions attempted: **11** (4 offline, 5 with `--network`, 1 with
  `--network --runtime-checks`, 1 with both on a temporary copy)
- Reproductions that installed successfully: **2** (napari on the clone, pint on a temporary copy)
- Runtime checks exercised on a real project: **1** (napari: 2 modules imported, pytest absent, collection skipped)

No quality score is computed, and none of these numbers is a grade of the tool:
they are counts of what was done.

## Beta criteria

| # | Criterion | Result |
| --- | --- | --- |
| 1 | 5/5 scans complete without a crash | **pass** |
| 2 | 5/5 suggests complete without a crash | **pass** |
| 3 | no known incorrect `SAFE` suggestion | **pass** (one class was found and fixed; none outstanding) |
| 4 | no unexpected modification of an analysed project | **pass** |
| 5 | JSON and Markdown reports written for every run | **pass** |
| 6 | CLI consistent (exit codes, help, destinations) | **pass** (one inconsistency found and fixed) |
| 7 | internal test suite green | **pass** (522 passed, 1 skipped) |
| 8 | no project-specific rule in the source | **pass** (every fix is generic, with a synthetic regression test) |
| 9 | false positives reviewed and classified | **pass** (78/78 warnings) |
| 10 | every crash-shaped input becomes a regression test | **pass** (no crash was found) |

All ten criteria pass. Two of them only pass *because* of what this task found:
criterion 3 (the CRLF patch) and criterion 6 (`--apply` with `--rollback`).

**A failed project is not a failed tool.** Four of the five projects could not
be installed from a copy without `.git`, and that is a fact about their release
process, reported as RC150 and RC401 with the backend's own error text. The beta
gate is about ReproCheck's behaviour on those projects, not about their
reproducibility.

## Conda environments: TASK-016 addendum

The same five clones, re-scanned after the Conda source was added. This section
answers two questions the earlier exercise could not: which of these projects
actually declares a Conda environment, and what does the tool make of it.

| Project | environment file | name | Python | channels | Conda deps | pip subsection | findings |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [pint](https://github.com/hgrecco/pint) | none | - | - | - | 0 | 0 | none |
| [tqdm](https://github.com/tqdm/tqdm) | environment.yml | 	qdm | python >=3.8 | conda-forge, defaults | 30 | 5 | 1 × RC232 |
| [astropy](https://github.com/astropy/astropy) | none | - | - | - | 0 | 0 | none |
| [mne-python](https://github.com/mne-tools/mne-python) | environment.yml | mne | python >=3.11 | conda-forge | 66 | 3 | 1 × RC232 |
| [napari](https://github.com/napari/napari) | none | - | - | - | 0 | 0 | none |

Two of the five declare an environment, and both were read. **No error was
raised on any project**: no RC230, no RC231, no RC234, and the two RC232
observations are informational. All five clones finished with git status
--porcelain at 0 lines.

### Every finding, classified by hand

| Project | Finding | Evidence | Classification |
| --- | --- | --- | --- |
| tqdm | RC232 | environment.yml:11 declares ipywidgets unbounded; pyproject [notebook] declares ipywidgets>=6. Witness: 6.0 | **reasonable** |
| tqdm | RC232 | environment.yml:12 declares setuptools; pyproject [build-system] declares setuptools>=42 | **false positive - fixed** |
| mne-python | RC232 | environment.yml:45 declares pillow unbounded; pyproject [test] declares pillow >= 10.2. Witness: 10.2.0 | **reasonable** |

**The false positive, and what it was.** [build-system] requires is installed
in an *isolated build environment* used to assemble the wheel. It is not what
runs. The project's own scope table already said a uild declaration is never
compared with anything, and the first version of the Conda check did compare
it, which produced a finding about two unrelated things. The check now excludes
uild, the message names the section it compared against so a reader can judge
scope, and 	est_build_system_requirements_are_never_compared is the regression
test, written from a synthetic fixture rather than from tqdm.

**The two remaining findings are useful, not noise.** Both say the Conda
environment does not pin something the project's metadata requires a minimum of.
Creating the environment as written can leave the project with a version its own
metadata rules out. Neither is a defect in the project, and both are info.

### Performance

The Conda source reads at most a handful of small YAML files, and that cost was
measured rather than assumed, on the largest clone available.

| Project | Before (55e01e4) | After | Difference |
| --- | --- | --- | --- |
| mne-python | 5.789 s (median of 3) | 5.836 s (median of 3) | **+0.047 s, +0.8%** |

The before figure comes from a git worktree of the commit preceding this work,
run in a separate process, so the two trees are compared without either
disturbing the other. +0.8% on the largest project is not worth optimising, and
no micro-optimisation was attempted.

### What this addendum does not establish

- **No Conda environment was reproduced.** scan never runs conda, contacts a
  channel or solves anything, so none of these five environments was created,
  and nothing here says a Conda project reproduces. The report states this per
  project under *Not verified*.
- **Two of the five do not use Conda at all**, so the sample of environments is
  two, not five. Both are development environments that a contributor creates by
  hand, which is the most common shape; neither is a published, solved,
  locked environment.
- **No environment with a conflict was available in the wild.** RC230 and RC231
  are covered by the internal suite only, on synthetic fixtures built to make
  the conflict provable. That is a real gap in this validation.
- Platform selectors were not exercised by a real project either: neither of
  the two files uses a # [win] selector, so the anti-false-positive rule for
  them is covered by the internal suite only.

## What this validation does not establish

- No dynamic step was isolated at the operating-system level: Docker is not
  installed on this machine, so the build backends of five third-party projects
  ran with the current user's privileges inside the workspace copy.
- One commit per project, at one moment. Reproducibility drift over time was not
  observed; that needs a baseline history.
- The reproduction of a C-extension project (astropy) never got past the version
  step, so its native build was never exercised.
- `pytest --collect-only` was never reached on a real project, because none of
  the five installs runtime dependencies. The step is covered by the internal
  suite only.
- Only `pyproject.toml`-based projects were analysed. A `setup.py`-only or
  `setup.cfg`-based distribution is a known blind spot that none of these five
  exercises.
  - The classification of "reasonable warning" and "uncertain" is a judgement of
    one reviewer reading the evidence. It is recorded here so it can be disputed.

## Real Conda reproduction: TASK-018 addendum

TASK-016 read Conda environment files. TASK-017 added `reproduce --conda` and
validated it **only against a fake manager**, which is a fixture someone
remembers to keep honest. This section is the first run against a real solver,
and it found three things the fake had been getting wrong.

### The manager

| Field | Value |
| --- | --- |
| Manager | **micromamba 2.9.0** |
| Source | `mamba-org/micromamba-releases`, tag `2.9.0-0`, published 2026-08-07 |
| Asset | `micromamba-win-64.exe` |
| Published sha256 | `a6d804394b2418991c4e29562853eaace2f2ce9d9da661a98e74e02e8dbb44b0` |
| Verified sha256 | `a6d804394b2418991c4e29562853eaace2f2ce9d9da661a98e74e02e8dbb44b0` |
| Kept at | `C:\Projetos\ReproCheckBenchmarks\tools\micromamba\`, outside this repository |

The checksum is the one published beside the binary in the same GitHub release.
That proves the download is the artefact the publisher published, and it would
catch truncation or a corrupted mirror. It does **not** protect against a
compromised publisher account: the hash and the binary come from the same
place. There is no independent signature to check here, and the distinction is
recorded rather than glossed over.

The binary ships as `micromamba-win-64.exe`; discovery looks for `micromamba`,
so the file was copied to `micromamba.exe` with the hash unchanged. **The
product was not pointed at the path**: the tool directory was put on the `PATH`
of the benchmark process, which is what a user does. No local path appears in
`src/`, and discovery code is unchanged.

### Results

| Project | Manager | Python | Declared | Packages | pip check | Verdict | Duration | Repo |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| minimal fixture | micromamba 2.9.0 | 3.11.16 | `=3.11` | 20 | clean | PARTIAL | 24 s | read only |
| pip subsection | micromamba 2.9.0 | 3.11.16 | `==3.11` | 20 | clean | PARTIAL | 30 s | read only |
| runtime, no pytest | micromamba 2.9.0 | 3.11.16 | `==3.11` | 20 | clean | PARTIAL | 31 s | read only |
| runtime, with pytest | micromamba 2.9.0 | 3.11.16 | `==3.11` | 28 | clean | PARTIAL | 30 s | read only |
| **tqdm** | micromamba 2.9.0 | 3.13.15 | `>=3.8` | 341 | **not clean** | PARTIAL | 1300 s | unchanged |
| **mne-python** | micromamba 2.9.0 | 3.14.7 | `>=3.11` | 453 | clean | PARTIAL | 913 s | unchanged |

Repository integrity: `tqdm` at `9cf5a12b1f955468a17f0ba3c59092b23e4258ac` and
`mne-python` at `47d5be239f12eb310e7799b5119baf24bed89d05`, both on `master`,
`git status --porcelain` **0 lines before and after every run**, and no diff
against HEAD. The prefixes were created at `<workspace>/conda-env`; the projects
themselves were only read.

The network gate was proved before either solve. Both `--conda` runs without
`--network` stopped with **RC604**, created nothing, left no prefix, took
6 s and 145 s respectively (the difference is the static analysis of a large
repository, not a solve), and left both repositories untouched.

### Runtime checks, on a real environment

| | pytest available | collection | tests executed |
| --- | --- | --- | --- |
| environment without pytest | no | skipped, no finding | 0 |
| environment with pytest | yes | **186 collected** (tqdm) | **0** |

`pytest --collect-only` only. No test was executed, in any run.

On the Conda path the import smoke test has nothing to import: a
`conda env create` builds the environment the file describes and **does not
install the project into it**. The report says so in those words and lists the
step under *Not verified*, rather than presenting an empty list as a pass.

### Bugs found, and fixed generically

1. **A real Conda prefix on Windows keeps `python.exe` at the prefix root**, not
   in `Scripts`. The product assumed the virtual-environment layout, so an
   environment that had been created **successfully**, with exit code 0, was
   reported as a failure because the interpreter appeared to be missing. The
   interpreter path is now probed rather than assumed. This is the worst failure
   mode the task was looking for, and a fake built on `python -m venv` could not
   have found it.
2. **The import smoke test could never run on the Conda path**, because the fake
   manager installed the project into the prefix and a real solver never does.
   The fixture was corrected to install a non-project library instead, and the
   report now states the real reason.
3. **pip exits non-zero for a distribution it refuses, not only for a version
   mismatch.** The real tqdm environment produced `wcwidth 0.9.1 is not
   supported on this platform`. No pattern recognised it, so the report read
   "not clean" with zero conflicts named and no finding at all. The parser now
   knows that shape, and a failure it still cannot name says so instead of
   claiming a conflict.

Each has a regression test that fails without its fix, written from a synthetic
fixture, with no reference to micromamba or to these two projects.

### Fake versus real

| | What the fake did | What really happens |
| --- | --- | --- |
| prefix layout | `python -m venv`, `Scripts/python.exe` | Conda layout, `python.exe` at the root, plus `conda-meta/`, `Library/`, `etc/`, `include/`, `share/`, `Tools/`, `libs/`, `DLLs/` and the runtime DLLs beside it |
| the project | installed into the prefix | never installed |
| `conda list --json` | a bare array | `{"log_history": [], "packages": [...]}`; a package carries `base_url`, `build_number`, `dist_name`, `md5`, `platform`, `sha256`, `url` besides the four fields the product reads |
| `--version` | `24.11.1` | bare `2.9.0`, parsed correctly with no change |
| the `pip:` subsection | a package simply appears | **the solver satisfies it from a channel when it can.** `packaging>=23` was installed from `conda-forge` by the solver (`conda-meta` record, `INSTALLER=conda`), not from PyPI, and the report correctly attributes it to `conda-forge` |
| `pip check` | always either clean or a version mismatch | can also fail on a distribution pip refuses to install |
| pytest in the prefix | copied by hand, missing `_pytest` and `py` | the solver installs the whole closure |

The `packages` wrapper was already handled, so no change was needed there. A
package installed by pip is reported by conda and mamba with `channel: pypi`,
and a test now pins that, because flattening it into the channel found would
claim a PyPI install came from a Conda channel.

### Channels, cache and safety

Channels actually contacted, read from `conda-meta` in the finished prefixes:

| Project | Channels | Records |
| --- | --- | --- |
| tqdm | `conda-forge`, **`pkgs/main`** | 311 |
| mne-python | `conda-forge` | 451 |

`tqdm`'s `environment.yml` declares `conda-forge`, and the solve also used
`pkgs/main`. ReproCheck passes the declared channels and adds none, so this is
the manager's own default channel list, not a channel ReproCheck invented. It is
recorded because a reader deciding whether a reproduction is hermetic needs to
know that a declared-only channel set was not what happened.

Cache. The package cache was redirected out of the project with
`MAMBA_ROOT_PREFIX`, into `C:\Projetos\ReproCheckBenchmarks\tools\mamba-root\`
(8.3 GB, 168 595 files) and no environment was created there, so no solver
artefact lives inside this Git repository. **The repodata cache was not
redirected**: micromamba wrote 2 164 files (12.7 MB) into
`%LOCALAPPDATA%\conda` between 19:31 and 00:48, which is the user's own cache.
Nothing was deleted, as promised. The report never claims the user's cache is
untouched by a manager, because the manager wrote to it anyway.

The two tqdm solves were both cold, and the second was not fast because of it:
the first took 1300 s and the runtime-check run, which solved nothing new, took
467 s. mne-python took 913 s. ReproCheck's own overhead is small next to that:
the whole no-network run on mne-python, static analysis included, was 145 s.

Safety. Only the two public repositories above and synthetic fixtures were
used. No credentials were needed, none were supplied, and no secret was
injected. The subprocess environment clears `PYTHONPATH` and sets
`PYTHONNOUSERSITE`, so a prefix cannot import from the host. No container or
virtual machine was available on this machine, so there was **no operating
system isolation**: a Conda environment changes which packages are installed and
nothing about authority. See [reproduction-safety.md](reproduction-safety.md).

### What this section does not establish

- Only **micromamba** was exercised. `conda` and `mamba` are supported and
  untested against a real binary; their `--json` shapes are assumed to match.
- Windows only. The POSIX prefix layout, and `bin/python`, are covered by unit
  tests and by CI, not by a real solve here.
- The `pip:` subsection was **not** observed installing from PyPI, because the
  solver satisfied it from a channel. The `pypi` origin path is covered by a
  synthetic test, not by evidence.
- A timeout was never exercised. The budgets were 1800 s and 2400 s and neither
  was reached, so how a solve that overruns is reported is untested against a
  real manager.
- A failed real solve is untested. Every solve attempted here succeeded, so the
  RC602 path has no real-world evidence behind it.
- The mne environment resolved to Python 3.14.7 and tqdm to 3.13.15, both far
  from the versions those projects ship against. That is what their files
  declare, and it is reported rather than corrected.
