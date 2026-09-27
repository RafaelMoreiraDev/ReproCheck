"""Typed internal models used across the scanner, checks and reporters."""

from reprocheck.models.detected import (
    DetectedFile,
    PackageManagerHint,
    PythonRequirement,
    ReadmeCommand,
)
from reprocheck.models.finding import Confidence, Finding, Severity
from reprocheck.models.git import GitInfo
from reprocheck.models.project import ProjectScan
from reprocheck.models.report import ScanReport

__all__ = [
    "Confidence",
    "DetectedFile",
    "Finding",
    "GitInfo",
    "PackageManagerHint",
    "ProjectScan",
    "PythonRequirement",
    "ReadmeCommand",
    "ScanReport",
    "Severity",
]
