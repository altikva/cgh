"""Tests for codegraph.indexer — file indexing engine."""

import pytest

from codegraph.core.db import get_connection, reset_connection
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
        files = conn.find_nodes("File", return_fields=["path", "lang"])
        assert len(files) == 1
        assert files[0]["lang"] == "python"

    def test_index_creates_function_nodes(self, sample_python, tmp_path):
        index_file(sample_python, tmp_path)
        conn = get_connection(tmp_path)

        fn_names = [
            f["name"] for f in conn.find_nodes("Function", return_fields=["name"])
        ]
        assert "validate" in fn_names
        assert "main" in fn_names

    def test_index_creates_class_nodes(self, sample_python, tmp_path):
        index_file(sample_python, tmp_path)
        conn = get_connection(tmp_path)

        cls_names = [
            c["name"] for c in conn.find_nodes("Class", return_fields=["name"])
        ]
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
        data = conn.find_nodes("TFResource", return_fields=["name", "type"])
        assert any(d["name"] == "main" for d in data)

    def test_index_markdown(self, sample_markdown, tmp_path):
        ok = index_file(sample_markdown, tmp_path)
        assert ok is True

        conn = get_connection(tmp_path)
        titles = [
            s["title"] for s in conn.find_nodes("MdSection", return_fields=["title"])
        ]
        assert "Architecture" in titles

    def test_index_sql_ddl(self, tmp_path):
        f = tmp_path / "schema.sql"
        f.write_text(
            "CREATE TABLE users (\n  id INT PRIMARY KEY,\n  email TEXT\n);\n",
            encoding="utf-8",
        )
        ok = index_file(f, tmp_path)
        assert ok is True

        conn = get_connection(tmp_path)
        titles = [
            s["title"] for s in conn.find_nodes("MdSection", return_fields=["title"])
        ]
        assert "table:users" in titles

    def test_index_package_json(self, tmp_path):
        f = tmp_path / "package.json"
        f.write_text(
            '{"name": "demo", "scripts": {"build": "tsc"}}\n',
            encoding="utf-8",
        )
        ok = index_file(f, tmp_path)
        assert ok is True

        conn = get_connection(tmp_path)
        titles = [
            s["title"] for s in conn.find_nodes("MdSection", return_fields=["title"])
        ]
        assert "scripts" in titles
        assert "scripts.build" in titles

    def test_index_go_endpoints(self, tmp_path):
        f = tmp_path / "router.go"
        f.write_text(
            'package main\nfunc main() {\n    r.GET("/users", listUsers)\n}\n',
            encoding="utf-8",
        )
        ok = index_file(f, tmp_path)
        assert ok is True

        conn = get_connection(tmp_path)
        eps = conn.find_nodes("Endpoint", return_fields=["method", "path", "framework"])
        assert any(e["path"] == "/users" and e["framework"] == "gin" for e in eps)

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


class TestRootMismatchGuard:
    """A moved/foreign index must not be trusted by incremental reindex.

    Graph node paths are stored absolute; the blob shas are content-based and
    identical across machines, so a plain incremental after a move keeps every
    stale absolute path. The guard detects the root change (recorded in
    scan_meta) and forces a full rebuild that recomputes the paths.
    """

    @staticmethod
    def _git(root, *args):
        import subprocess

        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
        )

    def test_moved_index_forces_full_rebuild(self, tmp_path):
        import shutil

        from codegraph.indexer import incremental_reindex

        src = tmp_path / "src"
        src.mkdir()
        self._git(src, "init")
        (src / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        self._git(src, "add", "-A")
        self._git(src, "commit", "-m", "initial")
        reset_connection()
        index_repo(str(src))
        reset_connection()

        # Simulate shipping/moving the whole tree (index included) to a new path.
        dst = tmp_path / "moved"
        shutil.move(str(src), str(dst))
        reset_connection()

        result = incremental_reindex(str(dst))
        # The move is detected: a full rebuild ran instead of a blob-sha diff.
        assert result["mode"] == "fallback_full"

        # And every stored path now points at the new root, not the old one
        # (no orphaned old-root File nodes left behind). realpath normalizes
        # the macOS /var -> /private/var symlink on both sides.
        import os

        conn = get_connection(dst)
        paths = [
            os.path.realpath(p) for (p,) in conn.list_node_fields("File", ["path"])
        ]
        reset_connection()
        real_dst = os.path.realpath(dst)
        real_src = os.path.realpath(src)
        assert paths
        assert all(p.startswith(real_dst) for p in paths)
        assert not any(p.startswith(real_src) for p in paths)
