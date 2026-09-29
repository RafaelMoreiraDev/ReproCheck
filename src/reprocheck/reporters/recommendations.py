"""Deterministic next actions.

Each recommendation is a static sentence attached to a finding ID. There is no
model, no scoring and no ordering heuristic beyond severity, so two reports with
the same findings always produce the same recommendations.

The wording is deliberately non-prescriptive: it points at the next
investigation and never claims that a practice is universally required.
"""

from __future__ import annotations

from dataclasses import dataclass

from reprocheck.models.finding import Finding, Severity

_SEVERITY_RANK = {
    Severity.ERROR: 0,
    Severity.WARNING: 1,
    Severity.INFO: 2,
}


@dataclass(frozen=True, slots=True)
class Recommendation:
    """One next action attached to one finding ID."""

    finding_id: str
    title: str
    action: str

    def to_dict(self) -> dict[str, str]:
        return {
            "finding_id": self.finding_id,
            "title": self.title,
            "action": self.action,
        }


def _rec(finding_id: str, title: str, action: str) -> Recommendation:
    return Recommendation(finding_id=finding_id, title=title, action=action)


RECOMMENDATIONS: dict[str, Recommendation] = {
    # -- project basics -------------------------------------------------- #
    "RC001": _rec(
        "RC001",
        "No README was detected",
        "Add a README describing how to install and run the project, so a "
        "reader can reproduce it without asking the authors.",
    ),
    "RC002": _rec(
        "RC002",
        "No Python version was declared",
        "Check how a user is expected to choose an interpreter; an explicit "
        "requirement makes the reproduction target unambiguous.",
    ),
    "RC004": _rec(
        "RC004",
        "The directory is not a Git repository",
        "Investigate where the authoritative source lives: without VCS "
        "metadata, versions derived from tags or commits cannot be reproduced.",
    ),
    "RC005": _rec(
        "RC005",
        "The working tree is dirty",
        "Establish which uncommitted changes are meant to be part of the "
        "release, since a reproduction only sees committed state.",
    ),
    "RC007": _rec(
        "RC007",
        "No CI workflow was detected",
        "Check whether automation exists elsewhere; CI is usually the only "
        "place where the supported Python versions are stated.",
    ),
    "RC008": _rec(
        "RC008",
        "No .gitignore was detected",
        "Check how build artifacts are kept out of version control.",
    ),
    "RC009": _rec(
        "RC009",
        "Several package managers are present",
        "Decide which tool is authoritative, so documentation, CI and local "
        "installs cannot disagree.",
    ),
    # -- Python version consistency -------------------------------------- #
    "RC101": _rec(
        "RC101",
        "A local Python version conflicts with a declared one",
        "Compare the local interpreter with the declared requirement and "
        "update whichever is wrong.",
    ),
    "RC102": _rec(
        "RC102",
        "CI and project files declare different Python versions",
        "Reconcile the two declarations; CI is what most users will run.",
    ),
    "RC103": _rec(
        "RC103",
        "No overlap between CI and declared Python versions",
        "Check whether the declared range can ever be satisfied in CI.",
    ),
    "RC104": _rec(
        "RC104",
        "A Python requirement could not be parsed",
        "Read the requirement by hand: an unparsable specifier is treated as "
        "no constraint at all.",
    ),
    # -- package managers and lockfiles ---------------------------------- #
    "RC110": _rec(
        "RC110",
        "Several lockfiles are present",
        "Keep the lockfile of the authoritative tool and remove or document "
        "the others.",
    ),
    "RC111": _rec(
        "RC111",
        "A lockfile has no declaration file",
        "Restore or remove the lockfile; it cannot be refreshed without its "
        "declaration file.",
    ),
    "RC112": _rec(
        "RC112",
        "A documented tool is not configured",
        "Check the documented install command; it will fail for anyone who "
        "follows the README.",
    ),
    "RC113": _rec(
        "RC113",
        "A lockfile is orphaned",
        "Restore the declaration file that produces this lockfile, or remove "
        "the lockfile.",
    ),
    "RC114": _rec(
        "RC114",
        "The documented install ignores the lockfile",
        "Check whether the documented command was meant to be reproducible; if "
        "so, the locked install belongs in the documentation.",
    ),
    "RC115": _rec(
        "RC115",
        "An install command is not declared in a project file",
        "Check where the documented dependencies are declared, or declare them "
        "in the project's manifest.",
    ),
    "RC116": _rec(
        "RC116",
        "A configured tool has no lockfile",
        "If exact versions matter, generate and commit a lockfile for the "
        "configured tool.",
    ),
    # -- paths and documentation ----------------------------------------- #
    "RC120": _rec(
        "RC120",
        "An absolute path appears in the project",
        "Replace it with a path relative to the project root; absolute paths "
        "only resolve on the machine that created them.",
    ),
    "RC121": _rec(
        "RC121",
        "A referenced local path does not exist",
        "Check whether the file is missing, ignored, or generated at install time.",
    ),
    "RC130": _rec(
        "RC130",
        "The README references a missing requirements file",
        "Fix the filename in the documentation or restore the file.",
    ),
    "RC131": _rec(
        "RC131",
        "The README references a script that does not exist",
        "Fix the path in the documentation or restore the script.",
    ),
    "RC132": _rec(
        "RC132",
        "The README references a missing directory",
        "Fix the path in the documentation or restore the directory.",
    ),
    "RC140": _rec(
        "RC140",
        "Generated artifacts are not ignored",
        "Review the generated artifact patterns and add them to .gitignore, so "
        "the tree stays clean and reproducible.",
    ),
    "RC150": _rec(
        "RC150",
        "The distribution version depends on Git metadata",
        "Reproduce with VCS metadata available if the exact package version "
        "matters, or record the released version explicitly.",
    ),
    # -- dependencies ----------------------------------------------------- #
    "RC200": _rec(
        "RC200",
        "Two requirements conflict",
        "Inspect the two declarations and decide which version is intended.",
    ),
    "RC201": _rec(
        "RC201",
        "Disjoint constraints",
        "Check whether the constraint file belongs to this project; disjoint "
        "constraints can never be satisfied together.",
    ),
    "RC202": _rec(
        "RC202",
        "A dependency is declared more than once",
        "Keep a single declaration per dependency, so the resolved version "
        "does not depend on file order.",
    ),
    "RC203": _rec(
        "RC203",
        "A runtime dependency has no version bound",
        "Decide whether the dependency should be bounded; an unbounded "
        "runtime dependency resolves differently over time.",
    ),
    "RC204": _rec(
        "RC204",
        "Pinned and unpinned dependencies are mixed",
        "Check whether the mixed policy is intentional: with both styles, the "
        "resolved set depends on the index state.",
    ),
    "RC205": _rec(
        "RC205",
        "Runtime and development dependencies diverge",
        "Compare both sets; a version present only in one of them is a likely "
        "source of a difference between environments.",
    ),
    "RC206": _rec(
        "RC206",
        "An included requirements file is missing",
        "Restore the included file or drop the include.",
    ),
    "RC207": _rec(
        "RC207",
        "A constraint file is never included",
        "Check whether the constraints are meant to apply; if so, include the "
        "file in the install path.",
    ),
    "RC208": _rec(
        "RC208",
        "A dependency is referenced from an external source",
        "Check whether the external source is expected to move; a URL resolves "
        "to whatever it serves at install time.",
    ),
    "RC209": _rec(
        "RC209",
        "A dependency points to a mutable VCS reference",
        "Check whether the exact commit is required; a branch or tag "
        "requirement resolves differently over time.",
    ),
    "RC210": _rec(
        "RC210",
        "A dependency points to a local path",
        "Check how a consumer without that path is expected to install the project.",
    ),
    # -- CI references ---------------------------------------------------- #
    "RC220": _rec(
        "RC220",
        "A CI action is referenced by a mutable tag",
        "Consider pinning the external GitHub Action/workflow to a full commit "
        "SHA if immutable CI inputs are required.",
    ),
    "RC221": _rec(
        "RC221",
        "The same action is referenced at different versions",
        "Align the references, so every workflow runs the same code.",
    ),
    "RC222": _rec(
        "RC222",
        "A CI reference to a local file is missing",
        "Check whether the workflow file is generated or was moved.",
    ),
    "RC223": _rec(
        "RC223",
        "A Docker image is referenced by a mutable tag",
        "Check whether the image digest matters; a tag resolves to a different "
        "image over time.",
    ),
    # -- Conda environment declarations ---------------------------------- #
    "RC230": _rec(
        "RC230",
        "The Conda environment and the project require different Python versions",
        "Decide which of the two is the intended support policy and align the "
        "other with it. ReproCheck proved the two accept no common version; "
        "it will not choose one for you.",
    ),
    "RC231": _rec(
        "RC231",
        "A dependency is declared incompatibly in the Conda environment",
        "Check which of the two constraints the code actually needs, then "
        "align the Conda declaration and the pip declaration on it.",
    ),
    "RC232": _rec(
        "RC232",
        "A dependency is declared differently but compatibly",
        "Confirm the difference is intended. A Conda channel may supply a "
        "build that pip cannot, in which case both declarations are correct.",
    ),
    "RC233": _rec(
        "RC233",
        "A package is declared both as Conda and as a pip requirement",
        "Decide which installer should provide it, so the solved environment "
        "is not left to the order conda happens to resolve the entries in.",
    ),
    "RC234": _rec(
        "RC234",
        "A Conda environment could not be read",
        "Repair the YAML. Until it parses, ReproCheck makes no claim about "
        "anything the environment declares, including its Python version.",
    ),
    "RC235": _rec(
        "RC235",
        "A pip subsection is present without pip being a Conda dependency",
        "Confirm this is deliberate. It often is, because conda installs pip "
        "transitively; the finding is informational, not a defect.",
    ),
    # -- reproduction ----------------------------------------------------- #
    "RC400": _rec(
        "RC400",
        "pip check found broken requirements",
        "Inspect the pip check log and reconcile the installed versions with "
        "the declared metadata.",
    ),
    "RC401": _rec(
        "RC401",
        "The installation failed",
        "Inspect the installation log before changing dependencies; the cause "
        "may be the index, the interpreter or the project itself.",
    ),
    "RC402": _rec(
        "RC402",
        "A required Python version is missing",
        "Install the required interpreter and reproduce again, or check whether "
        "the declared range is too narrow.",
    ),
    "RC403": _rec(
        "RC403",
        "No installation strategy was detected",
        "Check which file declares the project, or reproduce from an installed "
        "environment instead of from source.",
    ),
    "RC404": _rec(
        "RC404",
        "The installation needs a package index",
        "Re-run with --network if the index is reachable, and record that the "
        "attempt then depends on index state.",
    ),
    "RC405": _rec(
        "RC405",
        "The required Python version is ambiguous",
        "Remove the local override or install the declared version, so the "
        "selection is not guessed.",
    ),
    "RC406": _rec(
        "RC406",
        "The original project was modified",
        "Inspect the changed paths: they were not produced by ReproCheck, and "
        "they change what a later scan would see.",
    ),
    "RC407": _rec(
        "RC407",
        "The virtual environment could not be created",
        "Inspect the venv log; on Windows, long or non-ASCII paths are a common cause.",
    ),
    "RC500": _rec(
        "RC500",
        "A module failed to import",
        "Inspect the import log; the traceback shows which optional dependency "
        "or environment assumption is missing.",
    ),
    "RC501": _rec(
        "RC501",
        "Test collection failed",
        "Run `pytest --collect-only -q` in the reproduced environment to see "
        "which module prevents collection.",
    ),
    "RC502": _rec(
        "RC502",
        "A module import exceeded the timeout",
        "Check whether the module performs work at import time (downloads, "
        "training, network calls) before increasing any timeout.",
    ),
}


def build_recommendations(findings: list[Finding]) -> list[Recommendation]:
    """Return one recommendation per finding ID, in deterministic order."""
    seen: dict[str, Finding] = {}
    for finding in sorted(
        findings,
        key=lambda item: (_SEVERITY_RANK[item.severity], item.id, item.message),
    ):
        seen.setdefault(finding.id, finding)
    known = [
        RECOMMENDATIONS[finding_id]
        for finding_id in seen
        if finding_id in RECOMMENDATIONS
    ]
    return sorted(known, key=lambda item: item.finding_id)
