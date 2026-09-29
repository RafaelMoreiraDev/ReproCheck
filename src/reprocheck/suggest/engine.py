"""The suggestion engine.

A finding never implies a fix. Every finding that is a problem gets exactly one
of three answers, and the answer comes from a fixed table of rules -- there is
no model, no inference and no network:

* the rule knows an unambiguous, local, reversible edit  -> ``SAFE`` + patch;
* the rule knows a direction but not the missing fact   -> ``REVIEW_REQUIRED``;
* any patch would be speculation                          -> ``MANUAL_ONLY``.

A rule that returns nothing means "this finding has no deterministic
proposal", and the finding is listed under ``no_proposal`` so the report stays
traceable: every warning and error is either proposed for or explicitly
declined.

The engine only ever **reads**. It builds the ``after`` content and the unified
diff in memory; no file of the analysed project is opened for writing, renamed
or created.
"""

from __future__ import annotations

import codecs
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from reprocheck import __version__
from reprocheck.checks.gitignore import missing_artifact_patterns
from reprocheck.checks.workflows import origin as workflow_origin
from reprocheck.facts import Facts
from reprocheck.models import Confidence, Finding, ScanReport, Severity
from reprocheck.suggest import patches
from reprocheck.suggest.models import (
    FixSuggestion,
    Safety,
    SkippedFinding,
    SuggestionReport,
    SuggestionSummary,
)

GITIGNORE = ".gitignore"

#: Severities that deserve an answer. An ``info`` finding states that nothing
#: was proven wrong, so there is nothing to propose and nothing to decline.
_PROPOSABLE = {Severity.WARNING, Severity.ERROR}

_NO_RULE = "no deterministic proposal is defined for this check"

Rule = Callable[[Finding, "SuggestionContext"], FixSuggestion | None]
_RULES: dict[str, Rule] = {}


def rule(finding_id: str) -> Callable[[Rule], Rule]:
    def register(function: Rule) -> Rule:
        _RULES[finding_id] = function
        return function

    return register


@dataclass(frozen=True, slots=True)
class SuggestionContext:
    """Everything a rule may read. Nothing here can write."""

    root: Path
    facts: Facts

    def read_text(self, relative: str) -> str | None:
        """Read a file of the project verbatim, or ``None`` when impossible.

        Line endings and a byte-order mark are detected by the caller: a file
        whose bytes cannot be represented exactly is never patched.
        """
        path = self.root / relative
        if not path.is_file():
            return None
        with path.open("rb") as handle:
            raw = handle.read()
        if raw.startswith(codecs.BOM_UTF8):
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def has_bom(self, relative: str) -> bool:
        path = self.root / relative
        try:
            with path.open("rb") as handle:
                return handle.read(3) == codecs.BOM_UTF8
        except OSError:  # pragma: no cover - unreadable file
            return False

    def is_utf8(self, relative: str) -> bool:
        path = self.root / relative
        try:
            path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        return True

    def origins_in(self, evidence: str) -> tuple[str, ...]:
        """Return the workflow files whose location appears in ``evidence``.

        A CI finding reports its location as evidence rather than as ``file``.
        The locations are matched against the facts with the same function the
        check used to build them, so nothing is parsed out of a message.
        """
        found = {
            reference.file
            for reference in self.facts.workflow_references
            if workflow_origin(reference) in evidence
        }
        return tuple(sorted(found))


def suggest(facts: Facts, report: ScanReport) -> SuggestionReport:
    """Return every proposal for the findings of ``report``.

    ``facts`` is the read-only fact set the checks were derived from, so a rule
    never has to parse a message to know what it is fixing.
    """
    root = Path(report.project.path)
    context = SuggestionContext(root=root, facts=facts)
    proposals: list[FixSuggestion] = []
    skipped: list[SkippedFinding] = []
    counters: dict[str, int] = {}
    informational = 0

    findings = sorted(
        report.findings + report.reproduction_findings,
        key=lambda item: (item.id, item.file or "", item.evidence or ""),
    )
    for finding in findings:
        handler = _RULES.get(finding.id)
        if handler is None:
            if finding.severity in _PROPOSABLE:
                skipped.append(
                    SkippedFinding(
                        finding_id=finding.id,
                        reason=_NO_RULE,
                        severity=finding.severity.value,
                        file=finding.file,
                    )
                )
            else:
                informational += 1
            continue
        proposal = handler(finding, context)
        if proposal is None:
            skipped.append(
                SkippedFinding(
                    finding_id=finding.id,
                    reason="the rule found nothing to change in the current state",
                    severity=finding.severity.value,
                    file=finding.file,
                )
            )
            continue
        counters[proposal.finding_id] = counters.get(proposal.finding_id, 0) + 1
        proposals.append(
            _with_id(
                proposal,
                f"FIX-{proposal.finding_id}-{counters[proposal.finding_id]:03d}",
            )
        )

    summary = SuggestionSummary(
        findings=len(findings),
        fix_available=sum(1 for item in proposals if item.safety is Safety.SAFE),
        review_required=sum(
            1 for item in proposals if item.safety is Safety.REVIEW_REQUIRED
        ),
        manual_only=sum(1 for item in proposals if item.safety is Safety.MANUAL_ONLY),
        patches=sum(1 for item in proposals if item.has_patch),
        no_proposal=len(skipped),
        informational=informational,
    )
    return SuggestionReport(
        reprocheck_version=report.reprocheck_version or __version__,
        scan_timestamp=report.scan_timestamp,
        project=report.project.to_dict(),
        suggestions=tuple(proposals),
        skipped=tuple(skipped),
        summary=summary,
    )


def _with_id(suggestion: FixSuggestion, suggestion_id: str) -> FixSuggestion:
    """Stamp the deterministic identifier on a rule's proposal."""
    return replace(suggestion, suggestion_id=suggestion_id)


# --------------------------------------------------------------------------- #
# RC140 - the only patch this version produces
# --------------------------------------------------------------------------- #


@rule("RC140")
def gitignore_patterns(
    finding: Finding, context: SuggestionContext
) -> FixSuggestion | None:
    """Add the ignore patterns the detected tools require, as one patch.

    All missing patterns of a single finding become **one** proposal: they all
    land in the same file, and six separate patches for six patterns would be
    noise, not help.
    """
    missing = missing_artifact_patterns(context.facts)
    if not missing:
        return None
    if context.has_bom(GITIGNORE):
        return FixSuggestion(
            finding_id="RC140",
            title="Ignore patterns are missing from .gitignore",
            safety=Safety.REVIEW_REQUIRED,
            file=GITIGNORE,
            description=(f"The root .gitignore does not cover {', '.join(missing)}."),
            rationale=(
                "The file starts with a byte-order mark. ReproCheck will not "
                "propose a patch whose before/after text cannot represent the "
                "current bytes exactly."
            ),
            limitations=("the file is not plain UTF-8 text",),
        )
    content = context.read_text(GITIGNORE)
    if content is None:
        return FixSuggestion(
            finding_id="RC140",
            title="Ignore patterns are missing from .gitignore",
            safety=Safety.REVIEW_REQUIRED,
            file=GITIGNORE,
            description=(f"The root .gitignore does not cover {', '.join(missing)}."),
            rationale=(
                "The file could not be read as plain UTF-8 text, so a patch "
                "could not be built without risking a change to bytes ReproCheck "
                "cannot represent."
            ),
            limitations=("the file is not plain UTF-8 text",),
        )

    newline = patches.detect_newline(content)
    final_newline = patches.has_final_newline(content)
    after = patches.append_lines(content, list(missing), newline=newline)
    # The file is plain UTF-8 without a BOM here (checked above), so the text
    # encodes back to exactly the bytes that are on disk.
    before_bytes = content.encode("utf-8")
    after_bytes = after.encode("utf-8")
    limitations = [
        "Patterns are appended at the end of the file; no existing line is moved, "
        "reordered or removed.",
        "Only patterns proven missing by RC140 are proposed.",
    ]
    if not final_newline:
        limitations.append(
            "The file had no final newline; the proposal adds one so the last "
            "appended pattern is on its own line."
        )
    return FixSuggestion(
        finding_id="RC140",
        title="Add the missing artifact patterns to .gitignore",
        safety=Safety.SAFE,
        file=GITIGNORE,
        confidence=Confidence.HIGH.value,
        description=(
            f"Append {len(missing)} ignore pattern(s) to the root .gitignore: "
            + ", ".join(missing)
            + "."
        ),
        rationale=(
            "Each pattern is required by a tool detected in this project (a "
            "configured tool, a build backend or a documented virtual "
            "environment), and the check proved the file does not cover it. The "
            "change is a local, additive and reversible text edit: it cannot "
            "alter any code, dependency or behaviour."
        ),
        before=content,
        after=after,
        before_sha256=hashlib.sha256(before_bytes).hexdigest(),
        after_sha256=hashlib.sha256(after_bytes).hexdigest(),
        unified_diff=patches.unified(content, after, GITIGNORE),
        limitations=tuple(limitations),
    )


# --------------------------------------------------------------------------- #
# REVIEW_REQUIRED - a direction, no patch
# --------------------------------------------------------------------------- #


@rule("RC220")
def mutable_ci_reference(finding: Finding, context: SuggestionContext) -> FixSuggestion:
    """Name the direction, refuse to invent the commit."""
    locations = context.origins_in(finding.evidence or "")
    where = ", ".join(locations) if locations else "the reported location"
    return FixSuggestion(
        finding_id="RC220",
        title="Pin this CI reference to a verified full commit SHA",
        safety=Safety.REVIEW_REQUIRED,
        file=locations[0] if len(locations) == 1 else None,
        confidence=finding.confidence.value,
        description=(
            f"ReproCheck states the reference is not a full commit SHA. The "
            f"direction is a pinned SHA; the value is not knowable here. "
            f"Location: {where}."
        ),
        rationale=(
            "Which commit a tag such as 'v4' points at is external, temporal "
            "knowledge. ReproCheck performs no network request, so proposing a "
            "SHA would mean inventing one -- a patch that cannot be reviewed "
            "because it cannot be checked."
        ),
        limitations=(
            "the target commit of the current ref was not resolved",
            "no patch is generated",
        ),
    )


# --------------------------------------------------------------------------- #
# MANUAL_ONLY - the patch would be speculation
# --------------------------------------------------------------------------- #


def _manual(
    finding: Finding,
    title: str,
    description: str,
    rationale: str,
    limitations: tuple[str, ...],
) -> FixSuggestion:
    return FixSuggestion(
        finding_id=finding.id,
        title=title,
        safety=Safety.MANUAL_ONLY,
        file=finding.file,
        confidence=finding.confidence.value,
        description=description,
        rationale=rationale,
        limitations=limitations,
    )


@rule("RC116")
def uv_without_lockfile(finding: Finding, context: SuggestionContext) -> FixSuggestion:
    return _manual(
        finding,
        "Decide whether this project needs a lockfile",
        "A dependency tool is configured but ships no lockfile, so the resolved "
        "versions are not recorded by the repository.",
        "Generating a lockfile resolves real dependencies against an index. "
        "ReproCheck does no network request and would produce a file whose "
        "contents depend on the day it was written.",
        ("no lockfile is generated",),
    )


@rule("RC150")
def git_derived_version(finding: Finding, context: SuggestionContext) -> FixSuggestion:
    return _manual(
        finding,
        "Choose how the version is derived",
        "The distribution version comes from Git metadata, which a copy without "
        ".git cannot provide. Three directions exist: preserve the VCS metadata "
        "during the build, declare an explicit fallback version, or publish from "
        "a source archive carrying the right metadata.",
        "Each direction changes the release process, and ReproCheck cannot know "
        "which one the project intends. Any patch would invent a release policy.",
        (
            "no patch is generated",
            "the fallback version a backend chooses is not reproduced here",
        ),
    )


@rule("RC203")
def unbounded_dependency(finding: Finding, context: SuggestionContext) -> FixSuggestion:
    return _manual(
        finding,
        "Decide whether these dependencies should be bounded",
        "Some runtime dependencies are declared without a version constraint, so "
        "the resolved set depends on what the index serves at install time.",
        "Choosing a version bound is a maintenance decision, and ReproCheck "
        "queries no index: any specifier it wrote would be invented, and an "
        "invented pin is worse than no pin.",
        ("no version is chosen", "no patch is generated"),
    )


@rule("RC121")
def missing_local_path(finding: Finding, context: SuggestionContext) -> FixSuggestion:
    return _manual(
        finding,
        "Find out what this path should be",
        "A literal path passed to a file-reading call does not exist in the "
        "repository. It may be generated, downloaded, ignored, or simply wrong.",
        "ReproCheck cannot tell those apart, and the plausible repairs are "
        "mutually exclusive: creating the file, changing the path, or ignoring "
        "the finding. Any of them would be a guess about the project.",
        ("no path is invented", "no file is created"),
    )


def _readme_reference(finding: Finding, what: str, missing_kind: str) -> FixSuggestion:
    return _manual(
        finding,
        f"Find out whether the documentation or the {missing_kind} is wrong",
        f"The README references a {what} that does not exist in the repository.",
        f"ReproCheck cannot know which side is wrong: the documentation may "
        f"point at a {missing_kind} that was renamed, or the {missing_kind} may "
        f"be missing. Creating a {missing_kind} would invent project content, and "
        f"editing the README would remove information the author may have meant.",
        (f"no {missing_kind} is created", "the README is not edited"),
    )


@rule("RC130")
def missing_requirements(finding: Finding, context: SuggestionContext) -> FixSuggestion:
    return _readme_reference(finding, "requirements file", "requirements file")


@rule("RC131")
def missing_script(finding: Finding, context: SuggestionContext) -> FixSuggestion:
    return _readme_reference(finding, "script", "script")


@rule("RC132")
def missing_directory(finding: Finding, context: SuggestionContext) -> FixSuggestion:
    return _readme_reference(finding, "directory", "directory")


# --------------------------------------------------------------------------- #
# Conda: no proposal at all
# --------------------------------------------------------------------------- #
#
# Every Conda finding is MANUAL_ONLY, and none of them carries a patch.
# Choosing a Python version, a version constraint, a channel or the order of two
# installers is a project decision ReproCheck cannot make: the right value
# depends on what the project supports, what the platform binaries need, and
# what the maintainer intends. A patch here could be syntactically valid and
# still wrong, and a wrong environment is harder to notice than a broken one.


@rule("RC230")
def conda_python_conflict(
    finding: Finding, context: SuggestionContext
) -> FixSuggestion:
    return _manual(
        finding,
        "Decide which Python version this project supports",
        "ReproCheck proved that the Conda environment and the project "
        "requirement accept no common Python version. Which of the two to "
        "change, and to what, is a project decision.",
        rationale=(
            "Neither declaration is wrong on its own. Raising requires-python "
            "may abandon a supported platform; lowering the Conda pin may "
            "break code that uses a newer feature. ReproCheck does not know "
            "which the project needs."
        ),
        limitations=(
            "No version is proposed, because choosing one would be a guess "
            "about the project's support policy.",
            "Neither declaration is edited; only the conflict is stated.",
        ),
    )


@rule("RC231")
def conda_dependency_conflict(
    finding: Finding, context: SuggestionContext
) -> FixSuggestion:
    return _manual(
        finding,
        "Decide which constraint to change for the conflicting dependency",
        "ReproCheck proved that the Conda declaration and the pip "
        "declaration of the same package accept no common version.",
        rationale=(
            "The right constraint depends on what the code needs from that "
            "package, and on which of the two declarations is the intended "
            "one. Neither is knowable from the repository."
        ),
        limitations=("No version is proposed for either side.", "No file is modified."),
    )


@rule("RC232")
def conda_dependency_differs(
    finding: Finding, context: SuggestionContext
) -> FixSuggestion:
    return _manual(
        finding,
        "Decide whether the two declarations should be aligned",
        "The Conda and pip declarations of this package differ but are "
        "compatible. That may be intentional, for instance when a Conda "
        "channel supplies a build that pip cannot.",
        rationale=(
            "A difference that is compatible is not a defect. Aligning it "
            "would be a style change, and ReproCheck does not decide style."
        ),
        limitations=("No alignment is proposed.",),
    )


@rule("RC233")
def conda_pip_duplicate(finding: Finding, context: SuggestionContext) -> FixSuggestion:
    return _manual(
        finding,
        "Decide which installer should provide this package",
        "The package is listed both as a Conda dependency and inside the "
        "'pip' subsection.",
        rationale=(
            "Which one wins depends on the order conda solves the "
            "environment, and ReproCheck does not run conda. Removing either "
            "line could change the resulting environment."
        ),
        limitations=(
            "Neither declaration is removed.",
            "The duplicate is only reported.",
        ),
    )


@rule("RC234")
def conda_unreadable(finding: Finding, context: SuggestionContext) -> FixSuggestion:
    return _manual(
        finding,
        "Repair or remove this environment declaration",
        "The file is a Conda environment by name but could not be read, so "
        "nothing in it was checked.",
        rationale=(
            "A malformed or unreadable environment file is a defect in the "
            "project. Repairing it means understanding what the environment is "
            "meant to contain, which the file itself no longer states."
        ),
        limitations=(
            "The file is not rewritten; a YAML error is not safely fixable by pattern.",
            "ReproCheck makes no claim about the contents until the file parses.",
        ),
    )


@rule("RC235")
def conda_pip_not_declared(
    finding: Finding, context: SuggestionContext
) -> FixSuggestion:
    return _manual(
        finding,
        "Decide whether pip should be an explicit dependency",
        "The environment has a 'pip' subsection but does not list 'pip' "
        "among its Conda dependencies. This is frequently intentional.",
        rationale=(
            "conda often installs pip transitively, and a project may rely "
            "on that. Adding an explicit dependency changes the solved "
            "environment, and removing the subsection changes what is "
            "installed."
        ),
        limitations=("Neither the subsection nor the dependency list is edited.",),
    )


def supported_findings() -> tuple[str, ...]:
    """The finding IDs this version can answer something about."""
    return tuple(sorted(_RULES))
