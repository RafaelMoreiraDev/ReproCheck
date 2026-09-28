"""Tests for the local path checks (RC120-RC121)."""

from __future__ import annotations

from reprocheck.scanner import scan


def _findings(root, identifier: str) -> list:
    return [item for item in scan(root).findings if item.id == identifier]


def _finding(root, identifier: str):
    findings = _findings(root, identifier)
    return findings[0] if findings else None


# --------------------------------------------------------------------------- #
# 8. Windows absolute path
# --------------------------------------------------------------------------- #


def test_windows_absolute_path(make_project) -> None:
    root = make_project(
        {"conf/settings.py": 'DATA = "C:\\Users\\alice\\data\\input.csv"\n'}
    )
    finding = _finding(root, "RC120")

    assert finding is not None
    assert finding.file == "conf/settings.py"
    assert finding.line == 1
    assert r"C:\Users\alice\data\input.csv" in finding.message
    assert finding.confidence.name == "HIGH"


def test_windows_absolute_path_in_yaml(make_project) -> None:
    root = make_project(
        {".github/workflows/ci.yml": "env:\n  DATA: 'C:/data/input.csv'\n"}
    )
    finding = _finding(root, "RC120")
    assert finding is not None
    assert finding.file == ".github/workflows/ci.yml"


# --------------------------------------------------------------------------- #
# 9. Unix absolute paths
# --------------------------------------------------------------------------- #


def test_unix_home_path(make_project) -> None:
    root = make_project({"pkg/data.py": 'PATH = "/home/bob/datasets/train.csv"\n'})
    finding = _finding(root, "RC120")

    assert finding is not None
    assert "/home/bob/datasets/train.csv" in finding.message


def test_macos_home_path(make_project) -> None:
    root = make_project({"pkg/data.py": 'PATH = "/Users/bob/datasets/train.csv"\n'})
    assert _finding(root, "RC120") is not None


def test_same_path_in_two_files_is_one_finding(make_project) -> None:
    root = make_project(
        {
            "a.py": 'P = "/home/bob/data.csv"\n',
            "b.py": 'P = "/home/bob/data.csv"\n',
        }
    )
    findings = _findings(root, "RC120")

    assert len(findings) == 1
    assert "a.py:1" in findings[0].evidence
    assert "b.py:1" in findings[0].evidence


def test_ordinary_paths_are_not_reported(make_project) -> None:
    root = make_project(
        {
            "a.py": 'P = "data/input.csv"\n',
            "b.py": 'P = "/usr/bin/env"\n',
            "c.py": 'P = "relative/path.txt"\n',
        }
    )
    assert _findings(root, "RC120") == []


def test_virtualenvs_and_examples_are_skipped(make_project) -> None:
    root = make_project(
        {
            ".venv/lib/x.py": 'P = "/home/bob/data.csv"\n',
            "examples/demo.py": 'P = "/home/bob/other.csv"\n',
        }
    )
    assert _findings(root, "RC120") == []


# --------------------------------------------------------------------------- #
# 10./11. literal file references
# --------------------------------------------------------------------------- #


def test_missing_literal_reference(make_project) -> None:
    root = make_project(
        {"analysis/run.py": 'frame = pd.read_csv("dataset/testset.csv")\n'}
    )
    finding = _finding(root, "RC121")

    assert finding is not None
    assert "dataset/testset.csv" in finding.message
    assert finding.file == "analysis/run.py"
    assert finding.severity.name == "WARNING"
    assert finding.confidence.name == "MEDIUM"
    assert "runtime" in finding.message


def test_existing_literal_reference(make_project) -> None:
    root = make_project(
        {
            "analysis/run.py": 'frame = pd.read_csv("dataset/testset.csv")\n',
            "dataset/testset.csv": "a,b\n",
        }
    )
    assert _findings(root, "RC121") == []


def test_reference_resolved_next_to_the_source_file(make_project) -> None:
    root = make_project(
        {
            "analysis/run.py": 'data = open("local.json").read()\n',
            "analysis/local.json": "{}\n",
        }
    )
    assert _findings(root, "RC121") == []


def test_open_and_other_reading_calls_are_inspected(make_project) -> None:
    root = make_project(
        {
            "a.py": 'config = open("config/settings.json")\n',
            "b.py": 'handle = open("missing.txt")\n',
        }
    )
    messages = " ".join(item.message for item in _findings(root, "RC121"))
    assert "config/settings.json" in messages
    assert "missing.txt" in messages


def test_a_path_constructor_is_not_a_read(make_project) -> None:
    """`Path("out.png").unlink()` says nothing about a missing input file.

    Found while validating on real projects: every `Path(...)` literal was
    reported, including files a test creates and deletes.
    """
    root = make_project(
        {
            "a.py": (
                'ofile = Path("test_io.fits")\n'
                "if ofile.is_file():\n"
                "    ofile.unlink()\n"
                'assert pathlib.Path("mydask.png").exists()\n'
            )
        }
    )
    assert _findings(root, "RC121") == []


def test_the_call_name_must_not_be_the_tail_of_an_identifier(make_project) -> None:
    """`yaml.safe_load(...)` and `Path.joinpath(...)` are not file reads."""
    root = make_project(
        {
            "a.py": (
                "def load_yaml(lines):\n"
                '    return yaml.safe_load("\\n".join(lines))\n'
                'data = files(compat.__package__).joinpath("default_en.txt")\n'
                'value = mypath("other.txt")\n'
            )
        }
    )
    assert _findings(root, "RC121") == []


def test_a_dotted_reading_call_is_still_inspected(make_project) -> None:
    root = make_project({"a.py": 'frame = np.load("absent.npy")\n'})

    messages = " ".join(item.message for item in _findings(root, "RC121"))

    assert "absent.npy" in messages


def test_commented_out_code_is_not_a_reference(make_project) -> None:
    root = make_project(
        {
            "a.py": (
                "# load('leadfield.mat', 'G')\n"
                "    # open('also-missing.txt')\n"
                'handle = open("really-missing.txt")\n'
            )
        }
    )
    messages = " ".join(item.message for item in _findings(root, "RC121"))

    assert "really-missing.txt" in messages
    assert "leadfield.mat" not in messages
    assert "also-missing.txt" not in messages


def test_urls_and_variables_are_not_references(make_project) -> None:
    root = make_project(
        {
            "a.py": 'frame = read_csv("https://example.com/data.csv")\n',
            "b.py": 'frame = read_csv("$DATA_DIR/test.csv")\n',
            "c.py": 'frame = read_csv("data/*.csv")\n',
        }
    )
    assert _findings(root, "RC121") == []


def test_repeated_reference_is_reported_once(make_project) -> None:
    root = make_project(
        {
            "a.py": 'x = read_csv("missing/data.csv")\n',
            "b.py": 'y = read_csv("missing/data.csv")\n',
        }
    )
    findings = _findings(root, "RC121")
    assert len(findings) == 1
    assert "a.py:1" in findings[0].evidence


def test_facts_expose_resolution(make_project) -> None:
    root = make_project(
        {
            "a.py": 'x = read_csv("present.csv")\ny = read_csv("absent.csv")\n',
            "present.csv": "a\n",
        }
    )
    references = scan(root).facts["file_references"]

    assert [(item["value"], item["exists"]) for item in references] == [
        ("present.csv", True),
        ("absent.csv", False),
    ]


def test_a_drive_prefix_alone_is_not_a_path(make_project) -> None:
    """A placeholder after ``C:`` is a string, not a drive.

    Found while validating on real projects: the apostrophe of a prose
    contraction produced ``t:\\n{key}`` and a format placeholder produced
    ``s:\\n``; neither is a path.
    """
    root = make_project(
        {
            "a.py": (
                'raise ValueError(f"did not match:\\n{key} not in {other}")\n'
                "log.exception('pushing to branch %s:\\n\\nstdout: %s', name)\n"
            )
        }
    )
    assert _findings(root, "RC120") == []


def test_a_real_drive_path_is_still_reported(make_project) -> None:
    root = make_project({"a.py": 'p = "C:/Users/me/data.csv"\n'})

    messages = " ".join(item.message for item in _findings(root, "RC120"))

    assert "C:/Users/me/data.csv" in messages


def test_a_file_being_written_is_not_a_read(make_project) -> None:
    root = make_project(
        {
            "a.py": 'with open("generated.input", "w") as handle:\n    handle.write("x")\n'
        }
    )
    assert _findings(root, "RC121") == []


def test_a_project_api_named_open_is_not_a_read(make_project) -> None:
    root = make_project({"a.py": "viewer.open('mock_path.tif')\n"})

    assert _findings(root, "RC121") == []


def test_io_open_is_still_a_read(make_project) -> None:
    root = make_project({"a.py": 'handle = io.open("absent.json")\n'})

    messages = " ".join(item.message for item in _findings(root, "RC121"))

    assert "absent.json" in messages
