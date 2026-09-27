"""Static lists of paths the scanner looks for.

Keeping these in one place makes the detection surface explicit and easy to
extend in later versions.
"""

from __future__ import annotations

# Files whose presence is reported verbatim in ``detected_files``.
WATCH_FILES: dict[str, str] = {
    "pyproject.toml": "python-config",
    "setup.py": "python-config",
    "setup.cfg": "python-config",
    "requirements.txt": "python-config",
    "requirements-dev.txt": "python-config",
    "Pipfile": "python-config",
    "poetry.lock": "python-config",
    "uv.lock": "python-config",
    ".python-version": "python-config",
    "README.md": "docs",
    "README.rst": "docs",
    ".gitignore": "vcs",
}

# Directories whose presence is reported verbatim in ``detected_files``.
WATCH_DIRECTORIES: dict[str, str] = {
    "tests": "tests",
    "src": "source",
}

WORKFLOW_DIR = ".github/workflows"
WORKFLOW_SUFFIXES = (".yml", ".yaml")

# Directories never walked when counting Python files.
SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".nox",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        "node_modules",
        "build",
        "dist",
        "site-packages",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)
