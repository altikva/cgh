# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A file that disappears between the directory walk and its
#              indexing is skipped instead of aborting the whole scan.

from __future__ import annotations

from codegraph import indexer


def test_index_file_skips_a_file_gone_since_the_walk(tmp_path, monkeypatch):
    monkeypatch.setenv("CGH_DB", "sqlite")
    gone = tmp_path / "moved-away.md"

    assert indexer.index_file(gone, repo_root=tmp_path) is False


def test_index_repo_survives_a_file_vanishing_mid_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("CGH_DB", "sqlite")
    (tmp_path / "kept.py").write_text("def kept():\n    return 1\n")
    victim = tmp_path / "victim.py"
    victim.write_text("def victim():\n    return 2\n")

    real = indexer._index_file

    def vanish_then_index(path, *args, **kwargs):
        if str(path).endswith("victim.py") and victim.exists():
            victim.unlink()
        return real(path, *args, **kwargs)

    monkeypatch.setattr(indexer, "_index_file", vanish_then_index)

    indexer.index_repo(tmp_path)

    conn = indexer.get_connection(tmp_path)
    assert conn.function_names_in(str(tmp_path / "kept.py"))
