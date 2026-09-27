# Benchmark — Open Source Quartz Solar Forecast

External benchmark for ReproCheck. The target project is **not** part of this
repository and is never modified: ReproCheck only reads it.

- Target: `C:\Projetos\OpenClimateFix\open-source-quartz-solar-forecast`
- Commit scanned: `c07ad7402598979a7cd3c2eab7430098a3d56e78` (branch `main`, clean)
- ReproCheck version: `0.3.0` (TASK-003)
- Date: 2026-09-26

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
| `requires environment execution` | Needs an interpreter, an install or a test run |
| `requires network` | Needs a registry, a remote repository or a download |
| `requires semantic interpretation` | Needs a human judgement about intent |

## Results

| # | Known problem | Detection mode | Detected? | Check | Notes |
|---|---|---|---|---|---|
| OCF-B01 | Reusable workflows referenced by mutable branch (`@main`, `@issue/pip-all`) in 5 of 6 workflows | static | No | — | No CI-reference analysis implemented yet |
| OCF-B02 | First-party actions pinned to major tags (`actions/checkout@v2`, `actions/setup-python@v4`) instead of a SHA | static | No | — | Same gap as OCF-B01 |
| OCF-B03 | `[tool.uv]` configures uv but no `uv.lock` is committed, so dependency versions are not pinned by the repository | static | **Yes** | RC116 | Warning, high confidence |
| OCF-B04 | 5 of 15 runtime dependencies are unpinned (`typer`, `async_timeout`, `uvicorn`, `pydantic_settings`, `httpx`) while the others use `==` | static | **Yes** (new in V0.3) | RC203, RC204 | Info, high confidence; distribution reported as 9 pins / 1 range / 5 unconstrained |
| OCF-B05 | README documents `pip install -e .`, an install path that cannot consume any lockfile | static | No | RC114 did not fire | RC114 requires a lockfile to exist; here none does (see OCF-B03) |
| OCF-B06 | README documents `conda install -c conda-forge pyresample`, a package absent from the project metadata | static | **Yes** | RC115 | Warning, medium confidence |
| OCF-B07 | `.gitignore` covers `venv` but not `.venv`, the layout uv actually creates | static | **Yes** | RC140 | Listed among the uncovered patterns |
| OCF-B08 | `.gitignore` does not cover `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `build/`, `dist/` although those tools are configured | static | **Yes** | RC140 | Each pattern required by objective tool evidence |
| OCF-B09 | Required data is gitignored and absent (`quartz_solar_forecast/data`, `.../dataset/TZ-SAM/data`, `scripts/datapipes/*`) | requires semantic interpretation | No | — | Data provenance; would also need the network to verify a source |
| OCF-B10 | Version is dynamic from Git tags (`dynamic = ["version"]`, `dirty_template = "{tag}"`), so a dirty tree reuses the release version | static (partial) | No | — | The declaration is readable; proving the collision needs a build |
| OCF-B11 | `scripts/download_tz-sam.py` downloads a quarterly ZIP from a hardcoded URL with no checksum | requires network | No | — | The missing checksum is static, but the artifact itself is not inspected |
| OCF-B12 | Python version consistency | static | n/a | RC100 | Not a problem: 6 declarations, all consistent with `>=3.11` |
| OCF-B13 | `python-version: "['3.11']"` stringified list and `test_python_versions` env inputs to a reusable workflow | static | Partial | — | The value is parsed (3.11) but the fragile convention is not flagged |

**Score: 5 fully detected (OCF-B03, OCF-B04, OCF-B06, OCF-B07, OCF-B08),
1 partial (OCF-B13), 7 not detected.**

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

## Findings actually produced

```
Findings: 0 errors / 3 warnings / 1 info
  WARN RC115  README installs 'pyresample', absent from the project metadata
  WARN RC116  pyproject.toml [tool.uv] configures uv, but uv.lock is not present
  WARN RC140  .gitignore does not cover .mypy_cache/, .pytest_cache/, .ruff_cache/,
              .venv/, build/, dist/
  INFO RC100  6 Python version declaration(s), no inconsistency proven

Dependency findings: 0 errors / 0 warnings / 11 info
  (see the dependency audit table above)
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

## Read-only verification

`git status --porcelain` returned 0 lines and `HEAD` remained
`c07ad7402598979a7cd3c2eab7430098a3d56e78` before and after the scan.
