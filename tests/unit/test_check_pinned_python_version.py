"""Unit tests for scripts/check_pinned_python_version.py (issue #83)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_pinned_python_version as guard

REPO_ROOT = Path(__file__).resolve().parents[2]

CLASSIFIERS_THROUGH_313 = """\
[project]
name = "x"
requires-python = "{requires}"
classifiers = [
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
]
"""


def write_repo(tmp_path, pinned, requires=">=3.10", pyproject=None):
    (tmp_path / ".python-version").write_text(f"{pinned}\n")
    (tmp_path / "pyproject.toml").write_text(
        pyproject
        if pyproject is not None
        else CLASSIFIERS_THROUGH_313.format(requires=requires)
    )
    return tmp_path


class TestIsVersionAllowed:
    @pytest.mark.parametrize(
        "version, requires, expected",
        [
            # ">=3.10" contains the substring "3.10" and nothing else, so only
            # real parsing distinguishes these two.
            ("3.14", ">=3.10", True),
            ("3.9", ">=3.10", False),
            ("3.14", ">=3.10,<3.14", False),
            ("3.13", ">=3.10,<3.14", True),
            ("3.10", ">=3.10", True),
            ("3.10.0", ">=3.10", True),
            ("3.9.18", ">=3.10,<3.14", False),
            ("3.13", "<=3.13", True),
            ("3.13.1", "<=3.13", False),
            ("3.13", ">3.13", False),
            ("3.13.1", ">3.13", True),
            ("3.13", "==3.13", True),
            ("3.13", "!=3.13", False),
            ("3.13", "==3.13.*", True),
            ("3.13.4", "==3.13.*", True),
            ("3.12.4", "==3.13.*", False),
            ("3.12", ">=3.10,!=3.12.*", False),
            ("3.13", ">=3.10,!=3.12.*", True),
            ("3.13", "~=3.10", True),
            ("4.0", "~=3.10", False),
            ("3.9", "~=3.10", False),
        ],
    )
    def test_specifier_sets(self, version, requires, expected):
        assert guard.is_version_allowed(version, requires) is expected

    def test_whitespace_between_clauses_is_tolerated(self):
        assert guard.is_version_allowed("3.13", " >= 3.10 , < 3.14 ") is True

    def test_unknown_operator_is_loud(self):
        with pytest.raises(ValueError, match="Unrecognised version specifier"):
            guard.is_version_allowed("3.13", "=>3.10")


class TestReadPinnedVersion:
    @pytest.mark.parametrize(
        "contents, expected",
        [
            ("3.13\n", "3.13"),
            ("3.13", "3.13"),
            ("  3.13.1  \n", "3.13.1"),
            ("# the pin\n\n3.13\n", "3.13"),
            ("cpython-3.13.1\n", "3.13.1"),
            ("3.13t\n", "3.13"),
            # pyenv/uv allow several; the first is the one that gets used.
            ("3.13\n3.12\n", "3.13"),
        ],
    )
    def test_parses_real_world_files(self, tmp_path, contents, expected):
        path = tmp_path / ".python-version"
        path.write_text(contents)
        assert guard.read_pinned_version(path) == expected

    def test_empty_file_is_loud(self, tmp_path):
        path = tmp_path / ".python-version"
        path.write_text("\n# nothing here\n")
        with pytest.raises(ValueError, match="names no Python version"):
            guard.read_pinned_version(path)


class TestReadDeclaredSupport:
    def test_reads_both_halves(self, tmp_path):
        path = tmp_path / "pyproject.toml"
        path.write_text(CLASSIFIERS_THROUGH_313.format(requires=">=3.10"))
        support = guard.read_declared_support(path)
        assert support.requires_python == ">=3.10"
        assert support.classified_versions == [(3, 10), (3, 11), (3, 12), (3, 13)]

    def test_bare_major_classifier_is_not_a_version(self, tmp_path):
        """'Programming Language :: Python :: 3' declares no X.Y series."""
        path = tmp_path / "pyproject.toml"
        path.write_text(
            '[project]\nclassifiers = ["Programming Language :: Python :: 3"]\n'
        )
        assert guard.read_declared_support(path).classified_versions == []

    def test_absent_keys_read_as_empty(self, tmp_path):
        path = tmp_path / "pyproject.toml"
        path.write_text('[project]\nname = "x"\n')
        support = guard.read_declared_support(path)
        assert support.requires_python is None
        assert support.classified_versions == []

    def test_ignores_another_table(self, tmp_path):
        path = tmp_path / "pyproject.toml"
        path.write_text('[tool.other]\nrequires-python = ">=3.14"\n[project]\n')
        assert guard.read_declared_support(path).requires_python is None

    def test_fallback_reader_matches_tomllib_on_the_real_pyproject(self):
        """The 3.10 path must agree with the 3.11+ path on this repo's own file."""
        real = REPO_ROOT / "pyproject.toml"
        fallback = guard._project_table_without_tomllib(real)
        assert fallback["requires-python"] == ">=3.10"
        assert fallback["classifiers"] == guard._read_project_table(real)["classifiers"]


class TestCheckPinnedVersion:
    def test_in_range_passes(self, tmp_path):
        result = guard.check_pinned_version(write_repo(tmp_path, "3.13"))
        assert result.ok is True
        assert "3.13" in result.message

    def test_newer_than_every_classifier_fails(self, tmp_path):
        """The exact #83 breakage: 3.14 satisfies '>=3.10' but is unclassified."""
        result = guard.check_pinned_version(write_repo(tmp_path, "3.14"))
        assert result.ok is False
        assert "3.14" in result.message
        assert "classifier" in result.message
        assert "requires-python" not in result.message.split("Pin an")[0]

    def test_below_requires_python_fails(self, tmp_path):
        result = guard.check_pinned_version(write_repo(tmp_path, "3.9"))
        assert result.ok is False
        assert "requires-python" in result.message

    def test_patch_level_pin_is_matched_on_its_series(self, tmp_path):
        """Classifiers are X.Y; a 3.13.1 pin belongs to the 3.13 series."""
        assert guard.check_pinned_version(write_repo(tmp_path, "3.13.1")).ok is True

    def test_both_halves_violated_are_both_reported(self, tmp_path):
        repo = write_repo(tmp_path, "3.9", requires=">=3.10")
        message = guard.check_pinned_version(repo).message
        assert "requires-python" in message
        assert "classifier" in message

    def test_no_declaration_at_all_is_not_a_failure(self, tmp_path):
        repo = write_repo(tmp_path, "3.14", pyproject='[project]\nname = "x"\n')
        assert guard.check_pinned_version(repo).ok is True


class TestMain:
    def test_this_repo_passes_its_own_guard(self, capsys):
        """The check CI runs, run against the checked-in files."""
        assert guard.main(REPO_ROOT) == 0
        assert "within the declared support" in capsys.readouterr().out

    def test_failure_exits_nonzero_and_writes_to_stderr(self, tmp_path, capsys):
        assert guard.main(write_repo(tmp_path, "3.14")) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "does not claim" in captured.err
