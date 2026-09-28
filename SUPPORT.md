# Support

## Status

ReproCheck is a **beta**. The version on TestPyPI is a prerelease, the command
interface may change between prereleases, and the rule set is still growing. Use
it to look at a project and get a second opinion; do not wire it into a pipeline
that something else depends on yet.

## Requirements

- Python **3.11 or newer** (validated on 3.11, 3.12 and 3.13).
- No required external tool. Reading a project needs nothing beyond the install;
  reproducing an environment additionally needs the project's own build
  tooling.

## What to file, and where

Everything goes to <https://github.com/RafaelMoreiraDev/ReproCheck/issues>.

| I want to | Where |
| --- | --- |
| Report a bug | Issues, with the label `bug` |
| Ask how to do something | Issues, or Discussions if enabled |
| Request a feature | Issues, with the label `enhancement` |
| Report a false positive or a false negative | Issues, with the label `rules` |
| Report a security problem | See [SECURITY.md](SECURITY.md). Do not open a public issue. |
| Discuss a change before writing it | Open an issue first. |

A good bug report contains the `reprocheck --version` output, the Python
version, the exact command, the project layout that triggers it, and what you
expected instead. A false positive is much easier to act on with the report JSON
(`--json`) attached.

## Known limitations

The current beta limitations are listed at the end of
[CHANGELOG.md](CHANGELOG.md) and in the README's *Limitations* section. The ones
most likely to surprise you:

- `environment.yml` and Conda are not read. Dependencies are read from
  `pyproject.toml` and `requirements*.txt`.
- A project that gets its version from `.git` is not installable from a copy.
- Only one kind of fix can be applied (`RC140`), one suggestion at a time. There
  is no "apply all" and no interactive prompt.
- Workflow parsing is line-based, so YAML anchors, multi-line values and `env`
  indirection are not resolved.
- A project whose test suite contains path fixtures gets path findings about the
  fixtures themselves. There is deliberately no exclusion for `tests/`.
- `PASS` means the checks that ran succeeded. Anything under **Not verified** is
  still unknown.

## What ReproCheck is not

- **Not a sandbox.** A venv is not a security boundary. See
  [SECURITY.md](SECURITY.md) before pointing it at code you do not trust.
- **Not proof that a study is reproducible.** It reports static facts about a
  repository, plus what happened when the checks ran. Reproducibility is a
  property of a study, and no report can establish it.
- **Not a replacement for the test suite.** A passing suite and a clean
  ReproCheck scan are different things; neither implies the other.
- **Not a package vulnerability scanner.** It reads metadata and workflows. It
  does not resolve versions, does not query an advisory database, and does not
  know about CVEs.
- **Not a code formatter, linter or dependency resolver.** It does not rewrite
  your code beyond one `.gitignore` rule, and it does not upgrade anything.
- **Not supported as critical infrastructure.** Beta software, one maintainer,
  no stability or response-time guarantee.

## Before you file

1. Check the [CHANGELOG](CHANGELOG.md) for whether the behaviour changed.
2. Try the newest prerelease; a bug fixed for the next version may already be
   gone.
3. Include the report JSON when the issue is about a rule firing or not firing.
