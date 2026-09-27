"""Tests for the README reference checks (RC130-RC132)."""

from __future__ import annotations

from reprocheck.scanner import scan


def _finding(root, identifier: str):
    findings = [item for item in scan(root).findings if item.id == identifier]
    return findings[0] if findings else None


def _ids(root) -> set[str]:
    return {finding.id for finding in scan(root).findings}


def test_missing_requirements_file(make_project) -> None:
    root = make_project(
        {"README.md": "# x\n\n```bash\npip install -r requirements.txt\n```\n"}
    )
    finding = _finding(root, "RC130")

    assert finding is not None
    assert "requirements.txt" in finding.message
    assert finding.file == "README.md"
    assert finding.line == 4


def test_existing_requirements_file(make_project) -> None:
    root = make_project(
        {
            "README.md": "# x\n\n```bash\npip install -r requirements.txt\n```\n",
            "requirements.txt": "requests\n",
        }
    )
    assert "RC130" not in _ids(root)


def test_missing_script(make_project) -> None:
    root = make_project({"README.md": "# x\n\n```bash\npython scripts/train.py\n```\n"})
    finding = _finding(root, "RC131")

    assert finding is not None
    assert "scripts/train.py" in finding.message
    assert finding.confidence.name == "HIGH"


def test_existing_script(make_project) -> None:
    root = make_project(
        {
            "README.md": "# x\n\n```bash\npython scripts/train.py\n```\n",
            "scripts/train.py": "print('x')\n",
        }
    )
    assert "RC131" not in _ids(root)


def test_missing_test_path(make_project) -> None:
    root = make_project({"README.md": "# x\n\n```bash\npytest tests\n```\n"})
    assert _finding(root, "RC131") is not None


def test_directory_created_by_a_documented_clone_is_not_reported(make_project) -> None:
    root = make_project(
        {
            "README.md": (
                "# x\n\n```bash\n"
                "git clone https://example.com/org/open-source-quartz-solar-forecast.git\n"
                "cd open-source-quartz-solar-forecast\n"
                "pytest\n"
                "```\n"
            )
        }
    )
    assert "RC132" not in _ids(root)


def test_missing_directory_without_a_clone(make_project) -> None:
    root = make_project({"README.md": "# x\n\n```bash\ncd example_dir\n```\n"})
    finding = _finding(root, "RC132")

    assert finding is not None
    assert "example_dir" in finding.message


def test_existing_directory(make_project) -> None:
    root = make_project(
        {
            "README.md": "# x\n\n```bash\ncd src\npytest tests\n```\n",
            "src/a.py": "",
            "tests/test_a.py": "",
        }
    )
    ids = _ids(root)
    assert "RC132" not in ids
    assert "RC131" not in ids


def test_install_of_local_package_is_not_a_reference(make_project) -> None:
    root = make_project({"README.md": "# x\n\n```bash\npip install -e .\n```\n"})
    assert not {"RC130", "RC131", "RC132"} & _ids(root)


def test_commands_with_variables_are_skipped(make_project) -> None:
    root = make_project(
        {
            "README.md": (
                "# x\n\n```bash\npip install -r $REQUIREMENTS\n"
                "cd ${PROJECT_DIR}\npytest tests/unit\n```\n"
            )
        }
    )
    assert not {"RC130", "RC132"} & _ids(root)
    references = [
        item
        for item in scan(root).facts["readme_references"]
        if "$" not in item["value"]
    ]
    assert [item["value"] for item in references] == ["tests/unit"]


def test_python_module_execution_is_not_a_reference(make_project) -> None:
    root = make_project({"README.md": "# x\n\n```bash\npython -m pytest\n```\n"})
    assert "RC131" not in _ids(root)


def test_reference_facts_are_reported(make_project) -> None:
    root = make_project({"README.md": "# x\n\n```bash\npython scripts/train.py\n```\n"})
    references = scan(root).facts["readme_references"]

    assert references == [
        {
            "kind": "script",
            "value": "scripts/train.py",
            "command": "python scripts/train.py",
            "file": "README.md",
            "line": 4,
        }
    ]
