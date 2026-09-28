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
