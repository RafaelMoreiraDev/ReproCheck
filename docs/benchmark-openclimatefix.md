# Benchmark — Open Source Quartz Solar Forecast

External benchmark for ReproCheck. The target project is **not** part of this
repository and is never modified: ReproCheck only reads it.

- Target: `C:\Projetos\OpenClimateFix\open-source-quartz-solar-forecast`
- Commit scanned: `c07ad7402598979a7cd3c2eab7430098a3d56e78` (branch `main`, clean)
- ReproCheck version: `0.7.0` (TASK-007 report run; detection unchanged since V0.6)
- Date: 2026-09-27

The `OCF-Bxx` list below is an **external benchmark only**. It was reconstructed by
manual inspection of the target repository during TASK-002; it is not part of any
ReproCheck specification. No rule, name or path in `src/reprocheck/` encodes any
knowledge about these items or about this project.

> `OCF-Bxx` identifiers are local ReproCheck benchmark cases and are not
> upstream issue IDs.

## Detection mode

What a check would need in order to prove a case, independently of whether
ReproCheck implements it today:

| Mode | Meaning |
| --- | --- |
| `static` | Provable by reading files, with no execution, network or domain knowledge |
| `static (partial)` | The declaration can be read, but proving the consequence needs more |
| `environment execution` | Needs a virtual environment and an installation, no network required beyond the package index |
| `requires network` | Needs a registry, a remote repository or a download |
| `requires semantic interpretation` | Needs a human judgement about intent |

## Results

| # | Known problem | Detection mode | Detected? | Check | Notes |
|---|---|---|---|---|---|
| OCF-B01 | Reusable workflows referenced by mutable branch (`@main`, `@issue/pip-all`) in 5 of 6 workflows | static | **Yes** (new in V0.4) | RC220 | Six findings, one per distinct target+ref; `branch_ci.yml@main` occurs in two workflows and is reported once with both locations |
| OCF-B02 | First-party actions pinned to major tags (`actions/checkout@v2`, `actions/setup-python@v4`) instead of a SHA | static | **Yes** (new in V0.4) | RC220 | The message states mutability only; ReproCheck never claims a ref is obsolete |
| OCF-B03 | `[tool.uv]` configures uv but no `uv.lock` is committed, so dependency versions are not pinned by the repository | static | **Yes** | RC116 | Warning, high confidence |
| OCF-B04 | 5 of 15 runtime dependencies are unpinned (`typer`, `async_timeout`, `uvicorn`, `pydantic_settings`, `httpx`) while the others use `==` | static | **Yes** | RC203, RC204 | Info, high confidence; distribution reported as 9 pins / 1 range / 5 unconstrained |
| OCF-B05 | README documents `pip install -e .`, an install path that cannot consume any lockfile | static | No | RC114 did not fire | RC114 requires a lockfile to exist; here none does (see OCF-B03) |
| OCF-B06 | README documents `conda install -c conda-forge pyresample`, a package absent from the project metadata | static | **Yes** | RC115 | Warning, medium confidence |
| OCF-B07 | `.gitignore` covers `venv` but not `.venv`, the layout uv actually creates | static | **Yes** | RC140 | Listed among the uncovered patterns |
| OCF-B08 | `.gitignore` does not cover `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `build/`, `dist/` although those tools are configured | static | **Yes** | RC140 | Each pattern required by objective tool evidence |
| OCF-B09 | Required data is gitignored and absent (`quartz_solar_forecast/data`, `.../dataset/TZ-SAM/data`, `scripts/datapipes/*`) | requires semantic interpretation | No | — | Data provenance; would also need the network to verify a source |
| OCF-B10 | Version is dynamic from Git tags (`dynamic = ["version"]`, `dirty_template = "{tag}"`), so a dirty tree reuses the release version | static (partial) | **Partial** (V0.6) | RC150 | RC150 proves the version is derived from Git/VCS metadata and that a reproduction copy cannot derive it; the reproduction then measured the consequence (`0.0.1`). The "dirty tree reuses the release version" half still needs a build, so this is not a full detection |
| OCF-B11 | `scripts/download_tz-sam.py` downloads a quarterly ZIP from a hardcoded URL with no checksum | requires network | No | — | The missing checksum is static, but the artifact itself is not inspected |
| OCF-B12 | Python version consistency | static | n/a | RC100 | Not a problem: 6 declarations, all consistent with `>=3.11` |
| OCF-B13 | `python-version: "['3.11']"` stringified list and `test_python_versions` env inputs to a reusable workflow | static | Partial | — | The value is parsed (3.11); the fragile convention itself is still not flagged |

**Score: 7 fully detected (OCF-B01, OCF-B02, OCF-B03, OCF-B04, OCF-B06, OCF-B07,
OCF-B08), 2 partial (OCF-B10, OCF-B13), 4 not detected.**

Three of the remaining cases are `static` or `static (partial)`, so they remain
reachable without execution; OCF-B09 needs semantics and OCF-B11 needs the
network.

## Dependency audit (V0.3)

Declarations read from `pyproject.toml` only; the project has no
`requirements*.txt` and no lockfile.

| Kind | Count |
| --- | --- |
| runtime | 15 |
| dev | 17 (`dev` 9, `all` 7, `inverters` 1) |
| test | 0 |
| optional | 0 |
| build | 3 (`setuptools>=67`, `setuptools-git-versioning>=2.0,<3`, `wheel`) |
| unique packages | 27 |

Runtime declarations, verbatim:

```
apitally[fastapi]>=0.22.3   async-timeout   httpx   huggingface-hub==0.17.3
openmeteo-requests==1.2.0   pv-site-prediction==0.1.19   pydantic==2.6.2
pydantic-settings   python-dotenv==1.0.1   requests-cache==1.2.0
retry-requests==2.0.0   typer   uvicorn   xarray==2022.12.0   xgboost==2.0.3
```

Dependency findings actually produced:

```
0 errors / 0 warnings / 11 info

RC202  fastapi, gdown, huggingface-hub (x3), ocf-vrmapi, plotly, pytest, streamlit
       declared twice with the same constraint (dependency-group meta groups)
RC203  5 runtime dependencies have no version constraint
RC204  9 exact pins, 1 range and 5 unconstrained among runtime dependencies
```

No conflict was found: there is no `numpy`-style exact pin collision, no disjoint
range, no missing `-r`/`-c` include, no URL, VCS or local-path dependency. The
RC202 entries are all redundancy between the `all` meta group and the groups it
repeats — factual, and reported as info only.

## CI reference audit (V0.4)

Every `uses:` entry found in the six workflow files:

| File:line | Job | Target | Ref | Kind |
|---|---|---|---|---|
| `api_branch_ci.yaml:11` | `branch_ci` | `openclimatefix/.github/.github/workflows/branch_ci.yml` | `main` | reusable workflow |
| `merged_ci.yml:10` | `bump-tag` | `openclimatefix/.github/.github/workflows/bump_tag.yml` | `main` | reusable workflow |
| `publish.yaml:12` | `build` | `actions/checkout` | `v2` | action |
| `publish.yaml:15` | `build` | `actions/setup-python` | `v4` | action |
| `pytest.yaml:12` | `call-run-python-tests-integration` | `openclimatefix/.github/.github/workflows/python-test.yml` | `issue/pip-all` | reusable workflow |
| `pytest_unit.yaml:15` | `branch_ci` | `openclimatefix/.github/.github/workflows/branch_ci.yml` | `main` | reusable workflow |
| `tagged_ci.yml:15` | `tagged-ci` | `openclimatefix/.github/.github/workflows/tagged_ci.yml` | `main` | reusable workflow |

- external references: **7**
- pinned by a full commit SHA: **0**
- referenced by a mutable ref: **7**
- local references: **0**
- distinct targets with divergent refs: **0** (no RC221: every target is used with a single ref)
- local references pointing to a missing path: **0** (no RC222)

CI findings actually produced:

```
0 errors / 6 warnings / 0 info

RC220  actions/checkout                                     @v2
RC220  actions/setup-python                                 @v4
RC220  .../branch_ci.yml                                    @main   (2 locations)
RC220  .../bump_tag.yml                                     @main
RC220  .../python-test.yml                                  @issue/pip-all
RC220  .../tagged_ci.yml                                    @main
```

## Reproduction attempt (V0.5)

Two real attempts, both read-only for the target. No dataset was downloaded, no
evaluation, no training, no Hugging Face access: only environment installation.

### Without network (default)

```
python selected  3.11.16   lowest installed version matching .python-version 3.11
venv             <TEMP>/reprocheck/<run-id>/venv   (python 3.11.16, pip 24.0)
strategy         project   (pip install <workspace>/source)
install          exit 1 after 6.1s
pip check        not run
unchanged        yes (221 tracked files, 0 changed paths)
workspace        kept (failure policy)
```

Findings: `RC401` (installation failed) and `RC404` (the output proves the
dependencies were not available locally: `No matching distribution found for
setuptools>=67`, the build requirement of `[build-system]`). The attempt stopped
there, deterministically and without touching the target.

### With `--network`

```
python selected  3.11.16
venv             <TEMP>/reprocheck/<run-id>/venv   (python 3.11.16, pip 24.0)
strategy         project
install          exit 0 after 257s, quartz_solar_forecast installed
pip check        clean, 0 conflicts
unchanged        yes
workspace        removed (success policy)
```

Findings: **none**. The 15 declared runtime dependencies resolved and installed,
and `pip check` found no conflict in the reproduced environment.

Two observations that the reproduction produced and static analysis does not:

1. **The version became `0.0.1`.** `dynamic = ["version"]` is provided by
   `setuptools-git-versioning`, but the copy deliberately excludes `.git`, so the
   version could not be derived from a tag. This is the practical consequence of
   OCF-B10, now reported statically by RC150 and measured by the reproduction.
2. **The five unpinned dependencies installed without conflict** (`typer-0.27.2`,
   `uvicorn-0.54.0`, `httpx`, `pydantic-settings-2.2.1`, `async-timeout`), and
   `pyresample-1.34.2` arrived as a transitive dependency of `pv-site-prediction`.
   OCF-B04 and OCF-B06 are therefore real risks that did not materialise here,
   exactly as their `info`/`warning` severities claim.

### With `--network --runtime-checks` (V0.6)

A second attempt, again installation only: no dataset, no evaluation, no
training, no Hugging Face access, and no test execution.

```
python selected    3.11.16
install            exit 0 after 279s
installed          quartz_solar_forecast 0.0.1  (flagged as a fallback)
pip check          clean
import discovery   3 candidates, none invented
imports            api OK | dashboards OK | quartz_solar_forecast OK
pytest collection  not run: pytest is not installed in the reproduced venv
unchanged          yes (221 tracked files, 0 changed paths)
```

Findings: **no RC500, no RC501, no RC502**. The import smoke test imported all
three top-level packages the distribution actually ships. Collection was
skipped without an error, because `pytest` is a development dependency and
`pip install <source>` installs runtime dependencies only — ReproCheck does not
install pytest to make the step possible.

The three top-level names come from `importlib.metadata`, not from the
distribution name: `quartz_solar_forecast` is the distribution, and the wheel
ships `api/`, `dashboards/` and `quartz_solar_forecast/` as separate importable
packages. All three imported cleanly, so the package layout is coherent; the
version is not.

## Human-readable report (V0.7)

The same run was repeated with `0.7.0` to produce the two reports and the
verdict, again with `--network --runtime-checks` and nothing else:

```
python selected    3.11.16
install            exit 0 after 197s
installed          quartz_solar_forecast 0.0.1  (flagged as a fallback)
pip check          clean
imports            api OK | dashboards OK | quartz_solar_forecast OK
pytest collection  not run: pytest is not installed in the reproduced venv
unchanged          yes (221 tracked files, 0 changed paths)
verdict            PARTIAL
```

**Verdict: `PARTIAL`**, and the reason is a single line in the report:

```
5 open warning(s): RC115, RC116, RC140, RC150, RC220
```

That is the honest outcome. Every step ReproCheck can execute succeeded, so the
verdict is not `FAIL`; and the report is not `PASS`, because five objective
warnings are still open — a version derived from Git tags that a copy cannot
reproduce, a configured `uv` without a lockfile, six CI references on mutable
refs, uncovered artifacts and a documented `conda install` of a package the
project does not declare. `PASS` requires no open warning precisely so that this
project cannot be called fully verified.

What the report says is **not verified**, which is different from what failed:

| Item | Why |
| --- | --- |
| test collection | pytest is not installed in the reproduced environment (a development dependency) |
| full test suite | ReproCheck never executes tests |
| external datasets and model downloads | never accessed; the repository gitignores its data |
| remote URLs and VCS references | not fetched, so `download_tz-sam.py` and the clone URL were not resolved |
| CI action revisions | the seven action references are recorded but never resolved to a commit |
| published package index state | the installation resolved whatever the index served during this run |

The eight deterministic next actions are exactly the five warning IDs plus the
three info findings (`RC202`, `RC203`, `RC204`), one line each, changing nothing.
The report adds no claim of its own: it is the same facts as the JSON, arranged
for a reader who does not know the RC IDs. `README.md:80` and
`pyproject.toml [tool.uv]` are the two concrete places a reader is pointed at.

## Findings actually produced

```
Findings: 0 errors / 4 warnings / 1 info
  WARN RC115  README installs 'pyresample', absent from the project metadata
  WARN RC116  pyproject.toml [tool.uv] configures uv, but uv.lock is not present
  WARN RC140  .gitignore does not cover .mypy_cache/, .pytest_cache/, .ruff_cache/,
              .venv/, build/, dist/
  WARN RC150  the version is dynamic from setuptools-git-versioning, so a copy
              without .git cannot derive it (new in V0.6)
  INFO RC100  6 Python version declaration(s), no inconsistency proven

Dependency findings: 0 errors / 0 warnings / 11 info
  (see the dependency audit table above)

CI findings: 0 errors / 6 warnings / 0 info
  (see the CI reference audit table above)
```

No false positive was observed on this project. One candidate false positive was
found and removed during TASK-002: `cd open-source-quartz-solar-forecast`
(README line 226) is preceded by `git clone` in the same code block, so the
directory is created by the clone.

## What was correctly *not* reported

- `setuptools` (build backend) together with `uv` (installer) — a legitimate
  combination, not a conflict.
- `setuptools>=67` in `[build-system].requires` against anything in runtime:
  build requirements are installed in an isolated environment.
- No RC110: only one tool has a lockfile, and none is present.
- No RC120: the only absolute paths in the repository are inside `.venv/`, which
  is not scanned.
- No RC121: the project builds paths with `Path(__file__).parent`, so no literal
  relative reference exists to verify.
- No RC130/RC131: the two scripts referenced by the README
  (`scripts/forecast_csv.py`, `scripts/run_evaluation.py`) both exist.
- No RC101/RC102/RC103: `.python-version`, `pyproject.toml` and all four
  workflows agree on Python 3.11.
- No RC200/RC201/RC205: no dependency is declared twice with incompatible or
  divergent constraints.
- No RC220/RC221/RC222/RC223 beyond what is listed: no ref is SHA-pinned, no
  target is used with two different refs, no local reference is broken, and no
  Docker action is used. ReproCheck does not claim that `checkout@v2` is
  obsolete — that would need external, temporal knowledge.

## Read-only verification

`git status --porcelain` returned 0 lines and `HEAD` remained
`c07ad7402598979a7cd3c2eab7430098a3d56e78` before and after every scan and every
reproduction attempt, including the one that installed 80 packages.

See `docs/reproduction-safety.md` for what the reproduction does and does not
isolate.
