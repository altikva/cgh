"""Tests for .cghignore pattern matching."""

import pytest

from codegraph.indexer import _is_cghignored, _load_cghignore


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset the cghignore pattern cache between tests."""
    import codegraph.indexer as idx

    idx._cghignore_patterns = None
    yield
    idx._cghignore_patterns = None


class TestCghignore:
    def test_no_cghignore_file(self, tmp_path):
        result = _is_cghignored(tmp_path / "src" / "main.py", tmp_path)
        assert result is False

    def test_file_pattern(self, tmp_path):
        (tmp_path / ".cghignore").write_text("*.log\n")
        assert _is_cghignored(tmp_path / "debug.log", tmp_path) is True
        assert _is_cghignored(tmp_path / "main.py", tmp_path) is False

    def test_directory_pattern(self, tmp_path):
        (tmp_path / ".cghignore").write_text("vendor/\n")
        assert _is_cghignored(tmp_path / "vendor" / "lib.py", tmp_path) is True
        assert _is_cghignored(tmp_path / "src" / "lib.py", tmp_path) is False

    def test_glob_pattern(self, tmp_path):
        (tmp_path / ".cghignore").write_text("test_*.py\n")
        assert _is_cghignored(tmp_path / "test_main.py", tmp_path) is True
        assert _is_cghignored(tmp_path / "main.py", tmp_path) is False

    def test_comments_and_blank_lines(self, tmp_path):
        (tmp_path / ".cghignore").write_text("# comment\n\n*.tmp\n")
        patterns = _load_cghignore(tmp_path)
        assert patterns == ["*.tmp"]

    def test_multiple_patterns(self, tmp_path):
        (tmp_path / ".cghignore").write_text("*.log\n*.tmp\nvendor/\n")
        assert _is_cghignored(tmp_path / "app.log", tmp_path) is True
        assert _is_cghignored(tmp_path / "data.tmp", tmp_path) is True
        assert _is_cghignored(tmp_path / "vendor" / "x.py", tmp_path) is True
        assert _is_cghignored(tmp_path / "src" / "x.py", tmp_path) is False
