"""Tests for README command extraction."""

from __future__ import annotations

from reprocheck.scanners import readme

README = """\
# Example

Some prose with the word pip in it.

## Install

```bash
pip install -r requirements.txt
python -m pip install --upgrade pip
```

## Test

```console
$ pytest -q
# pytest is also used
```

## Notes

This is prose, not a code block.
"""


def test_commands_are_extracted_with_line_numbers(make_project) -> None:
    root = make_project({"README.md": README})
    found = readme.scan_readme_commands(root)

    assert [item.command for item in found] == [
        "pip install -r requirements.txt",
        "python -m pip install --upgrade pip",
        "pytest -q",
    ]
    assert all(item.file == "README.md" for item in found)
    assert [item.line for item in found] == [8, 9, 15]
    assert found[0].block_language == "bash"
    assert found[2].block_language == "console"


def test_prose_outside_blocks_is_ignored(make_project) -> None:
    root = make_project({"README.md": "# Title\n\nRun pip install first.\n"})
    assert readme.scan_readme_commands(root) == []


def test_unrelated_block_lines_are_ignored(make_project) -> None:
    root = make_project({"README.md": "```\nvalue = 42\n```\n"})
    assert readme.scan_readme_commands(root) == []


def test_rst_literal_block(make_project) -> None:
    root = make_project(
        {
            "README.rst": (
                "Title\n=====\n\nInstall::\n\n    pip install .\n    make test\n\n"
                "Done.\n"
            )
        }
    )
    found = readme.scan_readme_commands(root)
    assert [item.command for item in found] == ["pip install .", "make test"]
    assert all(item.file == "README.rst" for item in found)


def test_rst_code_block_directive_language(make_project) -> None:
    root = make_project(
        {"README.rst": ".. code-block:: console\n\n    uv sync\n    uv run pytest\n"}
    )
    found = readme.scan_readme_commands(root)

    assert [item.command for item in found] == ["uv sync", "uv run pytest"]
    assert all(item.block_language == "console" for item in found)


def test_readme_is_preferred_over_rst(make_project) -> None:
    root = make_project(
        {
            "README.md": "```bash\npoetry install\n```\n",
            "README.rst": "make::\n\n    make\n",
        }
    )
    found = readme.scan_readme_commands(root)
    assert [item.file for item in found] == ["README.md"]


def test_missing_readme(make_project) -> None:
    assert readme.scan_readme_commands(make_project({"a.py": ""})) == []
