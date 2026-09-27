"""Tests for the GitHub Actions reference checks (RC220-RC223)."""

from __future__ import annotations

from reprocheck.scanner import scan

SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"


def _workflow(body: str) -> dict[str, str]:
    return {".github/workflows/ci.yml": body}


def _ids(root) -> set[str]:
    return {finding.id for finding in scan(root).findings}


def _findings(root, identifier: str) -> list:
    return [item for item in scan(root).findings if item.id == identifier]


def _finding(root, identifier: str):
    found = _findings(root, identifier)
    assert found, f"{identifier} not reported"
    return found[0]


# --------------------------------------------------------------------------- #
# 1./2./3. mutability
# --------------------------------------------------------------------------- #


def test_mutable_ref_is_reported(make_project) -> None:
    root = make_project(
        _workflow("jobs:\n  b:\n    steps:\n      - uses: actions/checkout@v4\n")
    )
    finding = _finding(root, "RC220")

    assert finding.severity.name == "WARNING"
    assert finding.confidence.name == "HIGH"
    assert "actions/checkout" in finding.message
    assert "v4" in finding.message
    assert "full commit SHA" in finding.message
    assert ".github/workflows/ci.yml:4" in finding.evidence


def test_full_sha_is_not_reported(make_project) -> None:
    root = make_project(
        _workflow(f"jobs:\n  b:\n    steps:\n      - uses: actions/checkout@{SHA}\n")
    )
    assert "RC220" not in _ids(root)


def test_mutable_branch_is_reported(make_project) -> None:
    root = make_project(
        _workflow("jobs:\n  b:\n    steps:\n      - uses: someorg/action@main\n")
    )
    finding = _finding(root, "RC220")

    assert "'main'" in finding.message
    # The message states mutability, never that the ref is obsolete.
    assert "obsolete" not in finding.message
    assert "outdated" not in finding.message
    assert "unsafe" not in finding.message


def test_reference_without_ref_is_reported(make_project) -> None:
    root = make_project(
        _workflow("jobs:\n  b:\n    steps:\n      - uses: someorg/action\n")
    )
    assert "without any ref" in _finding(root, "RC220").message


def test_one_finding_per_target_and_ref(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/a.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: org/action@main\n"
            ),
            ".github/workflows/b.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: org/action@main\n"
            ),
        }
    )
    findings = _findings(root, "RC220")

    assert len(findings) == 1
    assert "a.yml:4" in findings[0].evidence
    assert "b.yml:4" in findings[0].evidence


# --------------------------------------------------------------------------- #
# 10./11./12. divergence between refs
# --------------------------------------------------------------------------- #


def test_different_refs_for_the_same_target(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/a.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: actions/checkout@v2\n"
            ),
            ".github/workflows/b.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: actions/checkout@v4\n"
            ),
        }
    )
    finding = _finding(root, "RC221")

    assert finding.severity.name == "WARNING"
    assert finding.confidence.name == "HIGH"
    assert "v2" in finding.message
    assert "v4" in finding.message
    assert "does not decide" in finding.message


def test_same_ref_for_the_same_target_is_not_reported(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/a.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: actions/checkout@v4\n"
            ),
            ".github/workflows/b.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: actions/checkout@v4\n"
            ),
        }
    )
    assert "RC221" not in _ids(root)


def test_sha_and_branch_for_the_same_target(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/a.yml": (
                f"jobs:\n  b:\n    steps:\n      - uses: org/action@{SHA}\n"
            ),
            ".github/workflows/b.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: org/action@main\n"
            ),
        }
    )
    ids = _ids(root)

    assert "RC221" in ids
    assert "RC220" in ids


def test_different_targets_do_not_collide(make_project) -> None:
    root = make_project(
        _workflow(
            "jobs:\n  b:\n    steps:\n"
            "      - uses: actions/checkout@v2\n"
            "      - uses: actions/setup-python@v2\n"
        )
    )
    assert "RC221" not in _ids(root)


# --------------------------------------------------------------------------- #
# 6./7./8./9. local references
# --------------------------------------------------------------------------- #


def test_existing_local_workflow(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n  call:\n    uses: ./.github/workflows/reusable.yml\n"
            ),
            ".github/workflows/reusable.yml": "name: reusable\njobs: {}\n",
        }
    )
    assert "RC222" not in _ids(root)


def test_missing_local_workflow(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n  call:\n    uses: ./.github/workflows/reusable.yml\n"
            )
        }
    )
    finding = _finding(root, "RC222")

    assert finding.severity.name == "WARNING"
    assert finding.confidence.name == "HIGH"
    assert "reusable.yml" in finding.message
    assert "the workflow file" in finding.message


def test_existing_local_action(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: ./.github/actions/setup\n"
            ),
            ".github/actions/setup/action.yml": "name: setup\n",
        }
    )
    assert "RC222" not in _ids(root)


def test_missing_local_action(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: ./.github/actions/setup\n"
            ),
            ".github/actions/setup/README.md": "docs only\n",
        }
    )
    finding = _finding(root, "RC222")

    assert "action.yml" in finding.message


def test_local_directory_without_action_file(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: ./.github/actions/setup\n"
            ),
            ".github/actions/setup/Dockerfile": "FROM alpine\n",
        }
    )
    assert "RC222" in _ids(root)


def test_local_reference_outside_the_project(make_project) -> None:
    root = make_project(
        {
            ".github/workflows/ci.yml": (
                "jobs:\n  b:\n    steps:\n      - uses: ../outside/action\n"
            )
        }
    )
    assert "RC222" in _ids(root)


# --------------------------------------------------------------------------- #
# docker
# --------------------------------------------------------------------------- #


def test_docker_with_mutable_tag(make_project) -> None:
    root = make_project(
        _workflow("jobs:\n  b:\n    steps:\n      - uses: docker://alpine:latest\n")
    )
    finding = _finding(root, "RC223")

    assert finding.severity.name == "WARNING"
    assert "alpine" in finding.message
    assert "digest" in finding.message


def test_docker_with_digest_is_not_reported(make_project) -> None:
    digest = "sha256:" + "b" * 64
    root = make_project(
        _workflow(f"jobs:\n  b:\n    steps:\n      - uses: docker://alpine@{digest}\n")
    )
    ids = _ids(root)

    assert "RC223" not in ids
    assert "RC220" not in ids


def test_docker_is_not_reported_as_a_plain_action(make_project) -> None:
    root = make_project(
        _workflow("jobs:\n  b:\n    steps:\n      - uses: docker://alpine:3.19\n")
    )
    assert "RC220" not in _ids(root)


# --------------------------------------------------------------------------- #
# combined behaviour
# --------------------------------------------------------------------------- #


def test_ci_findings_use_their_own_category(make_project) -> None:
    root = make_project(
        _workflow("jobs:\n  b:\n    steps:\n      - uses: actions/checkout@v4\n")
    )
    finding = _finding(root, "RC220")

    assert finding.category == "ci-references"
    assert finding.to_dict()["category"] == "ci-references"


def test_no_workflow_produces_no_ci_findings(make_project) -> None:
    root = make_project({"a.py": ""})
    ids = _ids(root)

    assert not {"RC220", "RC221", "RC222", "RC223"} & ids
    assert scan(root).workflow_references == []


def test_sha_pinned_workflow_is_quiet(make_project) -> None:
    root = make_project(
        _workflow(
            f"jobs:\n  b:\n    steps:\n      - uses: org/action@{SHA}\n"
            f"      - uses: org/other@{OTHER_SHA}\n"
        )
    )
    assert not {"RC220", "RC221", "RC222", "RC223"} & _ids(root)
