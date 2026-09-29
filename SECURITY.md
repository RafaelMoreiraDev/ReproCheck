# Security Policy

## Scope

ReproCheck is a pre-1.0 beta that **reads and repairs Python project
reproducibility metadata**. It is not a sandbox, and it is not a malware
scanner. This document exists because a tool that reads other people's
repositories and can run their build code has a real threat model, and that
model is the opposite of the usual one: **the projects you point ReproCheck at
are the untrusted input, not ReproCheck itself.**

The distribution has one runtime dependency, `packaging`, plus `PyYAML`, used to
read Conda environment files with `safe_load`. Neither makes any network request
during `scan`, `suggest`, `baseline` and `fix`, and neither sends anything
anywhere. Reports are written to files you name, plus a state directory under the
user's own application data.

## What ReproCheck executes

Two commands run code that belongs to the analysed project:

| Behaviour | Command | What runs |
| --- | --- | --- |
| Environment reproduction | `reprocheck reproduce` | The project's declared build backend (`setup.py`, `pyproject.toml` build requirements, `uv`, `pip`), which executes that project's own build scripts. |
| Conda reproduction | `reprocheck reproduce --conda --network` | A third-party **solver**, which resolves packages from channels and runs their install scripts. Any `pip:` subsection in the environment file runs a build backend too. |
| Runtime checks | `reprocheck scan --runtime-checks` | Imports the project's modules and runs pytest collection, executing their module-level code and import-time side effects. |

A Conda environment is not a sandbox either, and a solver is more capable than a
virtual environment: it downloads and executes packages on your behalf, from
channels you may not control, to satisfy a file you are auditing. That is why
`--conda` is opt-in, why `--network` is required on top of it, and why ReproCheck
**never installs a Conda manager** to make the feature work. If you do not have
`conda`, `mamba` or `micromamba` installed, the attempt stops and says so.

Everything else — `scan` without `--runtime-checks`, `suggest`, `baseline save`,
`baseline compare` — reads files and text. It does not import, execute or install
anything from the analysed project.

`fix` edits files. It is limited to one rule (`RC140`, appending missing
`.gitignore` patterns), it writes a backup plus a JSON audit record for every
change, and `fix --rollback <record-id>` restores the original bytes. **Take a
normal backup of anything you cannot lose anyway**, especially before the first
run against code you did not write.

## The virtual environment is not a sandbox

Creating a virtual environment changes which interpreter resolves an import. It
does **not** restrict what code is allowed to do. Code running in a venv runs
with your user's privileges and can read your files, use your network, and write
wherever you can write. Treat a venv as a version boundary, never as a trust
boundary.

## Recommendations for untrusted code

- Run against code you do not trust in a **container** (a Docker image with no
  credentials, no SSH agent and no host filesystem mounted) or in a **disposable
  VM** with a snapshot you can throw away.
- Do not mount your home directory, your SSH keys, your cloud credentials or a
  `~/.gitconfig` into such a container.
- Do not keep secrets in environment variables of an environment where you run
  someone else's build backend; child processes inherit them.
- Do not use `reprocheck reproduce` or `--runtime-checks` on untrusted code
  outside that boundary. Plain `scan` and `suggest` are the safer way to get a
  first opinion.
- Review the diff before accepting a fix, and keep the audit records.

## Reporting a vulnerability in ReproCheck

Report it through GitHub's private reporting on the repository, if that feature
is enabled:

- <https://github.com/RafaelMoreiraDev/ReproCheck/security/advisories/new>

Otherwise open an issue at
<https://github.com/RafaelMoreiraDev/ReproCheck/issues>.

Please include the version (`reprocheck --version`), the Python version, the
command you ran, and a minimal project that shows the problem. **Mark path
warnings for other people's projects as untrusted input**: a repository named in
a finding is attacker-controlled data, so a private report about it should not
quote it verbatim.

Useful reports include: a path in a repository that makes ReproCheck write or
delete a file outside the intended directory, a rollback that does not restore the
original content, a fix that produces a `.gitignore` that changes the meaning of
existing patterns, and a crash on a malformed or hostile project.

Please do not open a public issue for anything that needs a working exploit, and
do not include third-party exploit code in the report. A description of the
input that triggers it, and the observed behaviour, is enough.

## What is out of scope

- Findings in a project that ReproCheck is analysing. Those belong to that
  project, and its own maintainers, not here.
- Reports that depend on a malicious build backend already having arbitrary
  code execution on the host. That is the documented design, not a vulnerability.
- Denial of service through an intentionally enormous project.
