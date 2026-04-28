"""Tests for codegraph.indexer — file indexing engine."""

import pytest

from codegraph.core.db import get_connection, reset_connection
from codegraph.core.utils import rows
from codegraph.indexer import index_file, index_repo


@pytest.fixture(autouse=True)
def clean_db():
    """Reset DB connection between tests."""
    reset_connection()
    yield
    reset_connection()


class TestIndexFile:
    def test_index_python_file(self, sample_python, tmp_path):
        ok = index_file(sample_python, tmp_path)
        assert ok is True

        conn = get_connection(tmp_path)
        result = conn.execute("MATCH (f:File) RETURN f.path, f.lang")
        data = rows(result)
        assert len(data) == 1
        assert data[0]["f.lang"] == "python"

    def test_index_creates_function_nodes(self, sample_python, tmp_path):
        index_file(sample_python, tmp_path)
        conn = get_connection(tmp_path)

        result = conn.execute("MATCH (fn:Function) RETURN fn.name")
        data = rows(result)
        fn_names = [d["fn.name"] for d in data]
        assert "validate" in fn_names
        assert "main" in fn_names

    def test_index_creates_class_nodes(self, sample_python, tmp_path):
        index_file(sample_python, tmp_path)
        conn = get_connection(tmp_path)

        result = conn.execute("MATCH (c:Class) RETURN c.name")
        data = rows(result)
        cls_names = [d["c.name"] for d in data]
        assert "BaseHandler" in cls_names
        assert "DonationHandler" in cls_names

    def test_index_unsupported_extension(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00\x01\x02")
        ok = index_file(f, tmp_path)
        assert ok is False

    def test_index_terraform(self, sample_terraform, tmp_path):
        ok = index_file(sample_terraform, tmp_path)
        assert ok is True

        conn = get_connection(tmp_path)
        result = conn.execute("MATCH (r:TFResource) RETURN r.name, r.type")
        data = rows(result)
        assert any(d["r.name"] == "main" for d in data)

    def test_index_markdown(self, sample_markdown, tmp_path):
        ok = index_file(sample_markdown, tmp_path)
        assert ok is True

        conn = get_connection(tmp_path)
        result = conn.execute("MATCH (s:MdSection) RETURN s.title")
        data = rows(result)
        titles = [d["s.title"] for d in data]
        assert "Architecture" in titles

    def test_index_skips_unchanged(self, sample_python, tmp_path):
        # First index
        ok1 = index_file(sample_python, tmp_path)
        assert ok1 is True

        # Second index — same mtime, should skip
        ok2 = index_file(sample_python, tmp_path)
        assert ok2 is True  # returns True (already indexed)

    def test_force_reindex(self, sample_python, tmp_path):
        index_file(sample_python, tmp_path)
        ok = index_file(sample_python, tmp_path, force=True)
        assert ok is True


class TestIndexRepo:
    def test_index_repo_all_types(self, sample_repo):
        stats = index_repo(sample_repo)
        assert stats["indexed"] >= 4  # py, ts, tf, md
        assert stats["errors"] == 0
        assert "elapsed_s" in stats

    def test_index_repo_callbacks(self, sample_repo):
        files_seen = []

        def on_file(path, status, stats):
            files_seen.append((path, status))

        index_repo(sample_repo, on_file=on_file)
        assert len(files_seen) >= 4
        assert all(s == "indexed" for _, s in files_seen)
