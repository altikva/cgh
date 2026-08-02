# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Regressions for the code-quality audit fixes: config size /
#              ignore-pattern enforcement, same-file CALLS scoping, and
#              purge of stale inbound CALLS edges on re-index.

from __future__ import annotations

import pytest

from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_file


@pytest.fixture(autouse=True)
def clean_db():
    reset_connection()
    yield
    reset_connection()


def _write_config(root, body: str) -> None:
    cg = root / ".codegraph"
    cg.mkdir(exist_ok=True)
    (cg / "config.toml").write_text(body, encoding="utf-8")


def test_ignore_pattern_skips_file(tmp_path):
    # *.min.js is in DEFAULT_IGNORE_PATTERNS, so a minified file is skipped
    # even though .js is otherwise supported.
    f = tmp_path / "vendor.min.js"
    f.write_text("function a(){return 1}\n", encoding="utf-8")
    assert index_file(f, tmp_path) is False


def test_max_file_size_kb_skips_large_file(tmp_path):
    _write_config(tmp_path, "[codegraph]\nmax_file_size_kb = 1\n")
    big = tmp_path / "big.py"
    big.write_text("x = 1\n" * 2000, encoding="utf-8")  # ~12 KB > 1 KB cap
    assert index_file(big, tmp_path) is False


def test_size_cap_bypassed_by_force(tmp_path):
    _write_config(tmp_path, "[codegraph]\nmax_file_size_kb = 1\n")
    big = tmp_path / "big.py"
    big.write_text("def f():\n    return 1\n" + "x = 1\n" * 2000, encoding="utf-8")
    assert index_file(big, tmp_path, force=True) is True


def test_calls_prefer_same_file(tmp_path):
    # file_b defines helper(); file_a defines helper() AND caller() which
    # calls helper(). The CALLS edge must resolve to file_a's helper only.
    (tmp_path / "file_b.py").write_text(
        "def helper():\n    return 2\n", encoding="utf-8"
    )
    file_a = tmp_path / "file_a.py"
    file_a.write_text(
        "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n",
        encoding="utf-8",
    )
    index_file(tmp_path / "file_b.py", tmp_path)
    index_file(file_a, tmp_path)

    conn = get_connection(tmp_path)
    caller_id = f"{file_a}::caller"
    edges = conn.find_neighbors("CALLS", src_key=caller_id, return_dst=["id"])
    targets = {e["dst_id"] for e in edges}
    assert targets == {f"{file_a}::helper"}


def test_reindex_purges_stale_inbound_calls(tmp_path):
    # caller() calls helper(); re-index caller() with the call removed and
    # the inbound CALLS edge into helper must be gone (no ghost caller).
    helper = tmp_path / "helper.py"
    helper.write_text("def helper():\n    return 1\n", encoding="utf-8")
    caller = tmp_path / "caller.py"
    caller.write_text("def go():\n    return helper()\n", encoding="utf-8")
    index_file(helper, tmp_path)
    index_file(caller, tmp_path)

    conn = get_connection(tmp_path)
    helper_id = f"{helper}::helper"
    before = conn.find_neighbors("CALLS", dst_key=helper_id, return_src=["id"])
    assert any(e["src_id"] == f"{caller}::go" for e in before)

    # Rewrite caller with no call, re-index, and the inbound edge must vanish.
    caller.write_text("def go():\n    return 0\n", encoding="utf-8")
    index_file(caller, tmp_path, force=True)
    after = conn.find_neighbors("CALLS", dst_key=helper_id, return_src=["id"])
    assert not any(e["src_id"] == f"{caller}::go" for e in after)
