"""Tests for the GitHub Actions reference scanner (FACT layer)."""

from __future__ import annotations

from reprocheck.scanner import scan
from reprocheck.scanners import workflows

SHA = "0123456789abcdef0123456789abcdef01234567"


def _references(root) -> list:
    return workflows.scan_workflow_references(root)


def _one(root):
    found = _references(root)
    assert len(found) == 1, found
    return found[0]


# --------------------------------------------------------------------------- #
# 1. action by tag
# --------------------------------------------------------------------------- #


def test_action_with_major_tag(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "name: ci\n"
                "jobs:\n"
                "  build:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - uses: actions/checkout@v4\n"
            )
        }
    )
    reference = _one(root)

    assert reference.target == "actions/checkout"
    assert reference.ref == "v4"
    assert reference.reference_type == "action"
    assert reference.is_local is False
    assert reference.is_sha is False
    assert reference.is_mutable is True
    assert reference.job == "build"
    assert reference.line == 6
    assert reference.file == ".github/workflows/ci.yml"


# --------------------------------------------------------------------------- #
# 2. action pinned to a full SHA
# --------------------------------------------------------------------------- #


def test_action_pinned_to_a_full_sha(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": f"jobs:\n  b:\n    steps:\n      - uses: actions/checkout@{SHA}\n"
        }
    )
    reference = _one(root)

    assert reference.ref == SHA
    assert reference.is_sha is True
    assert reference.is_mutable is False


def test_short_sha_is_not_treated_as_pinned(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": "jobs:\n  b:\n    steps:\n      - uses: actions/checkout@0123456\n"
        }
    )
    reference = _one(root)

    assert reference.is_sha is False
    assert reference.is_mutable is True
    assert workflows.is_short_sha(reference.ref) is True
    assert workflows.is_full_sha(reference.ref) is False


# --------------------------------------------------------------------------- #
# 3./4. owner/action and subpaths
# --------------------------------------------------------------------------- #


def test_third_party_action(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": "jobs:\n  b:\n    steps:\n      - uses: someorg/some-action@main\n"
        }
    )
    reference = _one(root)

    assert reference.target == "someorg/some-action"
    assert reference.ref == "main"
    assert reference.reference_type == "action"


def test_action_subpath(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: github/codeql-action/upload-sarif@v2\n"
            )
        }
    )
    reference = _one(root)

    assert reference.target == "github/codeql-action/upload-sarif"
    assert reference.ref == "v2"
    assert reference.reference_type == "action"


def test_action_without_ref(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": "jobs:\n  b:\n    steps:\n      - uses: someorg/some-action\n"
        }
    )
    reference = _one(root)

    assert reference.target == "someorg/some-action"
    assert reference.ref == ""
    assert reference.is_mutable is True


# --------------------------------------------------------------------------- #
# 5. external reusable workflow
# --------------------------------------------------------------------------- #


def test_external_reusable_workflow(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n"
                "  call-tests:\n"
                "    uses: openclimatefix/.github/.github/workflows/python-test.yml@main\n"
            )
        }
    )
    reference = _one(root)

    assert reference.target == (
        "openclimatefix/.github/.github/workflows/python-test.yml"
    )
    assert reference.ref == "main"
    assert reference.reference_type == "reusable-workflow"
    assert reference.job == "call-tests"
    assert reference.is_local is False


# --------------------------------------------------------------------------- #
# 6./7. local reusable workflow
# --------------------------------------------------------------------------- #


def test_local_reusable_workflow(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n  call:\n    uses: ./.github/workflows/reusable.yml\n"
            ),
            ".github/workflows/reusable.yml": "name: reusable\njobs: {}\n",
        }
    )
    reference = _one(root)

    assert reference.is_local is True
    assert reference.reference_type == "local-workflow"
    assert reference.target == "./.github/workflows/reusable.yml"
    assert reference.is_mutable is False


# --------------------------------------------------------------------------- #
# 8./9. local actions
# --------------------------------------------------------------------------- #


def test_local_action_with_action_yml(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: ./.github/actions/setup\n"
            ),
            ".github/actions/setup/action.yml": "name: setup\n",
        }
    )
    reference = _one(root)

    assert reference.is_local is True
    assert reference.reference_type == "local-action"


def test_local_action_with_action_yaml(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: ./.github/actions/setup\n"
            ),
            ".github/actions/setup/action.yaml": "name: setup\n",
        }
    )
    assert _one(root).reference_type == "local-action"


# --------------------------------------------------------------------------- #
# 13. comments and quoting
# --------------------------------------------------------------------------- #


def test_commented_uses_is_ignored(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n"
                "  b:\n"
                "    steps:\n"
                "      # - uses: actions/checkout@v9\n"
                "      - uses: actions/checkout@v4  # pin me\n"
            )
        }
    )
    found = _references(root)

    assert len(found) == 1
    assert found[0].ref == "v4"


def test_quoted_value_is_unquoted(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: 'actions/checkout@v4'\n"
            )
        }
    )
    assert _one(root).target == "actions/checkout"


# --------------------------------------------------------------------------- #
# 14./15. job level and step level
# --------------------------------------------------------------------------- #


def test_step_name_is_recorded(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n"
                "  b:\n"
                "    steps:\n"
                "      - name: Check out\n"
                "        uses: actions/checkout@v4\n"
            )
        }
    )
    reference = _one(root)

    assert reference.job == "b"
    assert reference.step == "Check out"


def test_multiple_jobs_are_distinguished(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n"
                "  first:\n"
                "    steps:\n"
                "      - uses: actions/checkout@v4\n"
                "  second:\n"
                "    steps:\n"
                "      - uses: actions/setup-node@v4\n"
            )
        }
    )
    found = _references(root)

    assert [(item.job, item.target) for item in found] == [
        ("first", "actions/checkout"),
        ("second", "actions/setup-node"),
    ]


def test_steps_key_is_not_a_job(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: actions/checkout@v4\n"
            )
        }
    )
    assert _one(root).job == "b"


# --------------------------------------------------------------------------- #
# 16. workflow without uses
# --------------------------------------------------------------------------- #


def test_workflow_without_uses(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "name: ci\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n      - run: make test\n"
            )
        }
    )
    assert _references(root) == []
    assert scan(root).workflow_references == []


# --------------------------------------------------------------------------- #
# docker references
# --------------------------------------------------------------------------- #


def test_docker_with_tag(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: docker://alpine:3.19\n"
            )
        }
    )
    reference = _one(root)

    assert reference.reference_type == "docker"
    assert reference.target == "docker://alpine"
    assert reference.ref == "3.19"
    assert reference.is_mutable is True
    assert reference.is_sha is False


def test_docker_with_digest(make_project) -> None:
    digest = "sha256:" + "a" * 64
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                f"jobs:\n  b:\n    steps:\n      - uses: docker://alpine@{digest}\n"
            )
        }
    )
    reference = _one(root)

    assert reference.is_sha is True
    assert reference.is_mutable is False
    assert reference.ref == digest


# --------------------------------------------------------------------------- #
# report section
# --------------------------------------------------------------------------- #


def test_report_exposes_workflow_references(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: actions/checkout@v4\n"
            )
        }
    )
    references = scan(root).workflow_references

    assert len(references) == 1
    assert set(references[0]) == {
        "file",
        "line",
        "raw",
        "target",
        "ref",
        "reference_type",
        "job",
        "step",
        "is_local",
        "is_sha",
        "is_mutable",
    }


def test_non_workflow_files_are_not_parsed(make_project) -> None:
    root = make_project({"docs/ci.md": "uses: actions/checkout@v4\n"})
    assert _references(root) == []
