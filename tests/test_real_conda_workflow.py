"""The scheduled real-manager workflow, asserted on its own terms.

TASK-018 proved the Conda reproduction works against a real micromamba, and
TASK-019 gives that proof a schedule. The risk in a workflow like this is not
that it breaks: it is that it quietly stops being what it claims. A trigger
added by accident turns a weekly signal into a per-push gate, a
``cache-environment`` turns the tests into something that never solves anything,
and a Node 20 pin comes back one file at a time.

So the workflow is read as text and checked for those specific regressions. Text
rather than a parsed YAML tree, because the properties that matter here are
about the shape of the file: which triggers are present, which are absent, and
which action is pinned where. A parser would answer questions nobody is asking
and stay silent about the ones that are.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "real-conda.yml"
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

#: The file is read once at import. A missing file fails collection, which is a
#: louder failure than a test that quietly skips.
TEXT = WORKFLOW.read_text(encoding="utf-8")

#: The ``on:`` block, as the key it actually has in the file. YAML would read the
#: bare word ``on`` as the boolean true, which is a well-known trap for anyone
#: who reaches for a parser, and a reason to read the text.
ON_BLOCK = re.search(r"(?m)^on:\n((?:[ \t]+.*\n|\n)+)", TEXT)
SCHEDULE_BLOCK = re.search(r"(?m)^on:\n(?:[ \t]+.*\n)*?[ \t]+schedule:\n", TEXT)


def test_the_workflow_exists() -> None:
    assert WORKFLOW.is_file(), f"{WORKFLOW} is missing"
    assert TEXT.strip(), "the workflow is empty"


def test_it_runs_on_a_schedule() -> None:
    assert SCHEDULE_BLOCK, "no schedule trigger"
    cron = re.findall(r"cron:\s*\"?([^\"\n]+)\"?", ON_BLOCK.group(1))
    assert len(cron) == 1, f"expected one schedule, found {cron}"
    # Weekly, and not on the hour: every scheduled workflow in the world aims
    # for midnight and the queue is the worst part of a slow job.
    assert re.fullmatch(r"\d+ \d+ \* \* \d", cron[0]), cron[0]
    assert not cron[0].startswith("0 "), "do not schedule on the hour"
    assert cron[0].split()[1] != "0", "do not schedule on the hour"


def test_it_can_be_dispatched_by_hand() -> None:
    assert re.search(r"(?m)^on:\n(?:[ \t]+.*\n)*?[ \t]+workflow_dispatch:", TEXT)


def test_it_is_not_a_pull_request_gate() -> None:
    assert not re.search(r"(?m)^\s*pull_request\s*:", TEXT), (
        "the real-manager run needs a network and tens of minutes; it must not "
        "gate a pull request"
    )


def test_it_does_not_run_on_every_push() -> None:
    assert not re.search(r"(?m)^\s*push\s*:", TEXT), (
        "a push trigger would put a twenty-minute solve on the critical path of "
        "every commit"
    )


def test_it_runs_on_windows() -> None:
    assert re.search(r"(?m)^\s*runs-on:\s*windows-latest\s*$", TEXT)
    # Every real difference TASK-018 found was Windows specific, so running this
    # anywhere else would be claiming coverage nobody measured.
    for other in ("ubuntu-latest", "macos-latest", "macos-13"):
        assert other not in TEXT, f"{other} is not a validated target for this"


def test_the_job_has_a_timeout() -> None:
    match = re.search(r"(?m)^\s*timeout-minutes:\s*(\d+)\s*$", TEXT)
    assert match, "no timeout-minutes: a slow solve must not hold a runner"
    minutes = int(match.group(1))
    # TASK-018 measured 1300 s for a cold tqdm solve. Sixty minutes leaves room
    # for a cold cache and a slow solver, and still ends the same night.
    assert 30 <= minutes <= 90, minutes


def test_it_runs_only_the_real_manager_tests() -> None:
    assert re.search(r"pytest\s+-m\s+real_conda", TEXT), (
        "the point of this workflow is the real manager, not the whole suite"
    )
    # The full suite belongs to ci.yml, which runs it on three versions on every
    # push. Repeating it here spends the window proving that again.
    assert not re.search(r"(?m)^\s*run:\s*python -m pytest\s*$", TEXT), (
        "a bare pytest would run the whole suite"
    )


def test_the_default_suite_is_asserted_not_to_need_a_manager() -> None:
    # If the ordinary gate ever grows a dependency on a manager, the split has
    # rotted. Checking it here costs a collection and nothing else.
    assert re.search(r"-m\s+\"not real_conda\"", TEXT)


def test_every_external_action_is_a_full_sha() -> None:
    pattern = re.compile(
        r"uses:\s*([\w.-]+/[\w.-]+)@([0-9a-f]{40})\s*#\s*(v[\w.\-+]+)\s*$"
    )
    seen = 0
    for line in TEXT.splitlines():
        if "uses:" not in line:
            continue
        match = pattern.match(line.strip())
        assert match, f"unpinned or uncommented action reference: {line.strip()!r}"
        seen += 1
    assert seen, "no actions at all"


def test_no_action_is_on_a_node20_runtime() -> None:
    from test_packaging import EXPECTED_ACTIONS, NODE20_PINS

    pattern = re.compile(
        r"uses:\s*([\w.-]+/[\w.-]+)@([0-9a-f]{40})\s*#\s*(v[\w.\-+]+)\s*$"
    )
    for line in TEXT.splitlines():
        if "uses:" not in line:
            continue
        action, sha, _tag = pattern.match(line.strip()).groups()
        assert action in EXPECTED_ACTIONS, f"unaudited action {action}"
        stale = NODE20_PINS.get(action)
        if stale is not None:
            assert sha != stale, f"{action} is back on a Node 20 pin"
        assert EXPECTED_ACTIONS[action][2] in ("node24", "composite"), action


def test_the_shared_pins_match_the_other_workflows() -> None:
    """Two versions of one action in two workflows is exactly RC221."""

    def pins(text: str) -> dict[str, str]:
        found: dict[str, str] = {}
        for line in text.splitlines():
            if "uses:" not in line:
                continue
            match = re.match(r"uses:\s*([\w.-]+/[\w.-]+)@([0-9a-f]{40})", line.strip())
            if match:
                found[match.group(1)] = match.group(2)
        return found

    here, ci, release = pins(TEXT), pins(CI), pins(RELEASE)
    for action in (
        "actions/checkout",
        "actions/setup-python",
        "actions/upload-artifact",
    ):
        if action in ci or action in release:
            assert action in here, f"{action} is missing from real-conda.yml"
            expected = ci.get(action) or release.get(action)
            assert here[action] == expected, (
                f"{action}: real-conda.yml has {here[action]} and another "
                f"workflow has {expected}"
            )


def _code() -> str:
    """The workflow with its ``#`` comments removed.

    Prose in this file names the very things it must not do, because saying
    "no id-token here" is how a reader learns the rule. Checking the whole text
    for forbidden tokens would therefore fail on the explanation of the rule, so
    the checks that matter are made against the code a runner would act on. A
    comment cannot grant a permission.
    """
    stripped = re.sub(r"(?m)#.*$", "", TEXT)
    return "\n".join(line for line in stripped.splitlines() if line.strip())


def test_it_uses_no_secret_and_publishes_nothing() -> None:
    code = _code()
    for forbidden in (
        "secrets.",
        "id-token",
        "pypirc",
        "PYPI",
        "TWINE",
        "gh-action-pypi-publish",
        "packages: write",
    ):
        assert forbidden not in code, f"{forbidden} must not appear in the code"
    # Reading the repository and nothing else is the whole permission block.
    block = re.search(r"(?m)^permissions:\n((?:[ \t]+.*\n)+)", code)
    assert block, "no permissions block"
    granted = {
        line.split(":")[0].strip()
        for line in block.group(1).splitlines()
        if ":" in line
    }
    assert granted == {"contents"}, granted
    assert re.search(r"contents:\s*read", block.group(1))


def test_it_does_not_touch_the_publishing_environment() -> None:
    # release.yml owns Trusted Publishing. This workflow must not mention any of
    # it, so that a change here cannot quietly redirect a publish.
    code = _code()
    for token in ("pypi.org", "OIDC", "twine", "environment:"):
        assert token not in code, token


def test_the_cache_key_is_versioned_and_follows_the_fixtures() -> None:
    key = re.search(r"(?m)^\s*cache-downloads-key:\s*(.+?)\s*$", TEXT)
    assert key, "no cache key"
    value = key.group(1)
    # The manager version, so a different manager is a different cache.
    assert "2.9.0" in value, value
    # And a hash of the file that defines the fixtures, so a changed dependency
    # is never answered out of a stale cache.
    assert "hashFiles" in value, value
    assert "test_conda_reproduction.py" in value, value


def test_the_cache_holds_packages_and_not_the_environment() -> None:
    # A cached environment would let the tests pass without ever solving
    # anything, which is the one thing they exist to do.
    assert re.search(r"cache-downloads:\s*true", TEXT)
    assert not re.search(r"cache-environment:\s*true", TEXT)
    assert "cache-environment-key" not in TEXT


def test_the_manager_version_is_pinned_and_asserted() -> None:
    # The release **tag**, not `2.9.0`. The action validates the tag form and
    # rejects the bare version, which a first run of this workflow found out the
    # expensive way: the job died in eight seconds, which is at least a fast way
    # to find out.
    assert re.search(r'micromamba-version:\s*"2\.9\.0-0"', TEXT)
    assert not re.search(r'micromamba-version:\s*"2\.9\.0"', TEXT), (
        "the bare version is rejected by the action; the tag is 2.9.0-0"
    )
    assert not re.search(r"micromamba-version:\s*[\"']?latest", TEXT), (
        "latest would change under a schedule nobody reviews"
    )
    # Asserted at run time as well as pinned, so a silent change is loud.
    assert re.search(r"micromamba --version", TEXT)
    assert "::error::" in TEXT


def test_failure_artifacts_are_uploaded_only_on_failure() -> None:
    block = re.search(
        r"(?ms)- name: Upload the failure evidence\n(.*?)(?=\n  \w|\Z)", TEXT
    )
    assert block, "no failure artifact step"
    step = block.group(1)
    assert re.search(r"if:\s*failure\(\)", step), (
        "a successful run has no need for artifacts and no need to keep them"
    )
    assert "upload-artifact" in step
    # The environment is thousands of files and the source is a copy of somebody
    # else's repository. Neither helps anybody read a failure.
    assert "conda-env/**" in step
    assert "source/**" in step
    assert "logs/*.log" in step


def test_the_checkout_and_setup_python_pins_are_the_shared_ones() -> None:
    for action, sha in (
        ("actions/checkout", "08c6903cd8c0fde910a37f88322edcfb5dd907a8"),
        ("actions/setup-python", "e797f83bcb11b83ae66e0230d6156d7c80228e7c"),
    ):
        assert f"{action}@{sha}" in TEXT, f"{action} drifted from the other workflows"
    # And they keep the credential out of the git configuration afterwards.
    assert "persist-credentials: false" in TEXT


def test_a_superseded_run_is_not_cancelled() -> None:
    # Cancelling a twenty-minute solve to start another one wastes the window
    # instead of saving it, and the schedule is weekly enough that overlapping
    # runs are rare and cheap to let finish.
    block = re.search(r"(?m)^concurrency:\n((?:[ \t]+.*\n)+)", TEXT)
    assert block, "no concurrency block"
    assert re.search(r"cancel-in-progress:\s*false", block.group(1))


def test_it_documents_itself_as_a_signal_and_not_a_gate() -> None:
    header = TEXT[: TEXT.index("jobs:")]
    assert "not" in header and "required check" in header, (
        "a reader must not have to guess whether this blocks anything"
    )


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_the_other_workflows_are_untouched_by_this_one(workflow: str) -> None:
    """Guards the edits that would be tempting later."""
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    assert "real-conda" not in text, f"{workflow} must not depend on the scheduled run"
    if workflow == "ci.yml":
        assert "real_conda" not in text, (
            "the ordinary gate must not select the real-manager tests"
        )
    else:
        assert "id-token" in text, (
            "Trusted Publishing is release.yml's business and must stay intact"
        )
