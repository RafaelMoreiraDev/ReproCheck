"""ReproCheck: read-only reproducibility scanner for Python projects.

The version lives here and only here: ``pyproject.toml`` reads it through
``[tool.setuptools.dynamic]``, so the distribution metadata and the package can
never disagree. ``reprocheck --version`` prefers the installed metadata and
falls back to this constant when running from a source checkout.
"""

from __future__ import annotations

from importlib import metadata

__version__ = "0.11.0b1"

__all__ = ["__version__", "package_version"]


def package_version() -> str:
    """Return the version of the installed distribution, if there is one.

    The installed metadata is the authority, because it is what a user gets:
    reading it here means ``--version`` can never report something the
    installed package does not agree with. A source checkout without an install
    falls back to :data:`__version__`, which is the value the build reads.
    """
    try:
        return metadata.version("reprocheck")
    except metadata.PackageNotFoundError:
        return __version__
