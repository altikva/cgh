"""Tests for memory_index.py — scanning Claude Code memory files."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_memory_env(tmp_path: Path, monkeypatch) -> Path:
    """Create a fake memory dir + point CG_MEMORY_DIR at it."""
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    monkeypatch.setenv("CG_MEMORY_DIR", str(mem_dir))
    # Fresh FTS for each test
    (tmp_path / ".codegraph").mkdir(exist_ok=True)
    return mem_dir


def _write(path: Path, kind: str, title: str, body: str) -> None:
    path.write_text(
        f"---\nname: {title}\ntype: {kind}\n---\n\n# {title}\n\n{body}\n",
        encoding="utf-8",
    )


class TestScanMemoryDir:
    def test_indexes_files_by_kind(self, tmp_memory_env, tmp_path):
        _write(tmp_memory_env / "user_prefs.md", "user", "User preferences", "prefers pnpm")
        _write(tmp_memory_env / "feedback_commits.md", "feedback", "Commits", "use /commit skill")
        _write(tmp_memory_env / "project_domain.md", "project", "Domain", "donations platform")
        _write(tmp_memory_env / "reference_api.md", "reference", "API Ref", "see openapi.json")

        from codegraph.claude_state.memory import scan_memory_dir

        stats = scan_memory_dir(tmp_path)
        assert stats["indexed"] == 4
        assert stats["skipped"] == 0
        assert stats["removed"] == 0

    def test_classify_from_filename_fallback(self, tmp_memory_env, tmp_path):
        # No frontmatter — kind should come from filename prefix
        (tmp_memory_env / "feedback_no_fm.md").write_text("# Title\n\nbody", encoding="utf-8")
        from codegraph.claude_state.memory import classify

        kind, title = classify(tmp_memory_env / "feedback_no_fm.md")
        assert kind == "feedback"
        assert title == "Title"

    def test_mtime_skip(self, tmp_memory_env, tmp_path):
        _write(tmp_memory_env / "x.md", "user", "X", "body")
        from codegraph.claude_state.memory import scan_memory_dir

        scan_memory_dir(tmp_path)
        stats = scan_memory_dir(tmp_path)
        assert stats["indexed"] == 0
        assert stats["skipped"] == 1

    def test_removal_detection(self, tmp_memory_env, tmp_path):
        f = tmp_memory_env / "gone.md"
        _write(f, "user", "Gone", "will vanish")
        from codegraph.claude_state.memory import scan_memory_dir

        scan_memory_dir(tmp_path)
        f.unlink()
        stats = scan_memory_dir(tmp_path)
        assert stats["removed"] == 1

    def test_search_after_scan(self, tmp_memory_env, tmp_path):
        _write(
            tmp_memory_env / "feedback_donations.md",
            "feedback",
            "Donations",
            "prefer mobile money over stripe when currency is XAF",
        )
        _write(tmp_memory_env / "project_stack.md", "project", "Stack", "FastAPI + Nuxt 4")

        from codegraph.core.fts import get_fts_conn, memory_search
        from codegraph.claude_state.memory import scan_memory_dir

        scan_memory_dir(tmp_path)
        conn = get_fts_conn(tmp_path)
        hits = memory_search(conn, "mobile money")
        assert any("Donations" in h.title for h in hits)

        # kind filter
        hits = memory_search(conn, "fastapi", kind="project")
        assert hits and all(h.kind == "project" for h in hits)
