"""Tests for codegraph.core.utils — shared helper functions."""

from codegraph.core.utils import lang_color, rows, safe_id, short_path


class TestShortPath:
    def test_relative_path(self):
        assert (
            short_path("/home/user/project/src/main.py", "/home/user/project")
            == "src/main.py"
        )

    def test_same_path(self):
        assert short_path("/home/user/project", "/home/user/project") == "."

    def test_unrelated_path(self):
        result = short_path("/other/path/file.py", "/home/user/project")
        assert result == "/other/path/file.py"

    def test_pathlib_root(self):
        from pathlib import Path

        assert short_path("/a/b/c.py", Path("/a/b")) == "c.py"


class TestSafeId:
    def test_slashes(self):
        assert safe_id("src/main.py") == "src_main_py"

    def test_special_chars(self):
        result = safe_id("func(a, b)")
        assert "(" not in result
        assert ")" not in result
        assert "," not in result

    def test_truncation(self):
        long_name = "a" * 100
        assert len(safe_id(long_name)) == 60

    def test_spaces_and_quotes(self):
        result = safe_id("my 'function' here")
        assert " " not in result
        assert "'" not in result


class TestLangColor:
    def test_known_extensions(self):
        assert lang_color(".py") == "green"
        assert lang_color(".ts") == "blue"
        assert lang_color(".tf") == "magenta"
        assert lang_color(".md") == "cyan"

    def test_unknown_extension(self):
        assert lang_color(".xyz") == "white"
        assert lang_color(".rs") == "white"


class TestRows:
    def test_empty_result(self):
        class FakeResult:
            def get_column_names(self):
                return ["a", "b"]

            def has_next(self):
                return False

            def get_next(self):
                raise StopIteration

        assert rows(FakeResult()) == []

    def test_single_row(self):
        class FakeResult:
            def __init__(self):
                self._data = [["hello", 42]]
                self._idx = 0

            def get_column_names(self):
                return ["name", "count"]

            def has_next(self):
                return self._idx < len(self._data)

            def get_next(self):
                row = self._data[self._idx]
                self._idx += 1
                return row

        result = rows(FakeResult())
        assert result == [{"name": "hello", "count": 42}]

    def test_multiple_rows(self):
        class FakeResult:
            def __init__(self):
                self._data = [["a", 1], ["b", 2], ["c", 3]]
                self._idx = 0

            def get_column_names(self):
                return ["key", "val"]

            def has_next(self):
                return self._idx < len(self._data)

            def get_next(self):
                row = self._data[self._idx]
                self._idx += 1
                return row

        result = rows(FakeResult())
        assert len(result) == 3
        assert result[0] == {"key": "a", "val": 1}

    def test_exception_returns_empty(self):
        class BrokenResult:
            def get_column_names(self):
                raise RuntimeError("broken")

        assert rows(BrokenResult()) == []
