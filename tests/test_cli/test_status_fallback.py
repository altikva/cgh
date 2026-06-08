# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the cmd_status 3-tier counts fallback helpers,
#              _status_via_owner / _status_via_ro_open / _status_via_fts.
#              Covers the dict shape each helper returns and the two tiers
#              that work without a running owner (local RO open + FTS only).

"""
Tests for the `cgh status` 3-tier counts fallback.

The fallback ladder used to live inline in cmd_status, it now lives in three
named helpers. These tests drive the helpers directly against tmp_path repos.
They never spawn a real owner or server, so only the RO-open and FTS-only tiers
are exercised, both of which resolve without a live owner.
"""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

from codegraph.cli.commands_monitor import (
    _empty_status_source,
    _status_via_fts,
    _status_via_owner,
    _status_via_ro_open,
)
from codegraph.core.db import reset_connection

# The keys every status-source dict must carry, regardless of tier.
_SOURCE_KEYS = {"file_count", "endpoint_count", "counts_source", "fts_symbols"}


def _seed_graph(repo_root: Path) -> None:
    """Index a tiny repo on the default backend so a graph DB exists."""
    reset_connection()
    from codegraph.indexer import index_repo

    try:
        index_repo(str(repo_root))
    finally:
        reset_connection()


def _make_fts(repo_root: Path, n_symbols: int) -> None:
    """Build a minimal .codegraph/fts.db with a symbols table of n rows."""
    cg = repo_root / ".codegraph"
    cg.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(cg / "fts.db")
    c.execute("CREATE TABLE symbols (name TEXT)")
    c.executemany(
        "INSERT INTO symbols (name) VALUES (?)", [(f"s{i}",) for i in range(n_symbols)]
    )
    c.commit()
    c.close()


# ---------------------------------------------------------------------------
# Shared shape
# ---------------------------------------------------------------------------


def test_empty_source_shape():
    src = _empty_status_source()
    assert set(src) == _SOURCE_KEYS
    assert src == {
        "file_count": 0,
        "endpoint_count": 0,
        "counts_source": "none",
        "fts_symbols": None,
    }


def test_every_helper_returns_same_keys(tmp_path):
    # All three helpers, even on the "nothing resolved" path, return the
    # same dict shape so cmd_status can read it uniformly.
    for src in (
        _status_via_owner(str(tmp_path), False, None),
        _status_via_ro_open(str(tmp_path)),
        _status_via_fts(str(tmp_path)),
    ):
        assert set(src) == _SOURCE_KEYS


# ---------------------------------------------------------------------------
# Tier 1: owner
# ---------------------------------------------------------------------------


def test_owner_not_attempted_without_port(tmp_path):
    # owner_alive but no port -> tier short-circuits to "none".
    src = _status_via_owner(str(tmp_path), True, None)
    assert src["counts_source"] == "none"


def test_owner_not_attempted_when_dead(tmp_path):
    # port known but owner not alive -> tier short-circuits to "none".
    src = _status_via_owner(str(tmp_path), False, 1234)
    assert src["counts_source"] == "none"


# ---------------------------------------------------------------------------
# Tier 2: local read-only open
# ---------------------------------------------------------------------------


def test_ro_open_none_when_no_graph(tmp_path):
    # No .codegraph graph DB on disk -> RO open returns None -> "none".
    reset_connection()
    src = _status_via_ro_open(str(tmp_path))
    assert src["counts_source"] == "none"
    assert src["file_count"] == 0


def test_ro_open_counts_files(tmp_path):
    (tmp_path / "a.py").write_text(
        textwrap.dedent("""\
        def helper():
            return 1
    """)
    )
    (tmp_path / "b.py").write_text(
        textwrap.dedent("""\
        def other():
            return 2
    """)
    )
    _seed_graph(tmp_path)

    src = _status_via_ro_open(str(tmp_path))
    reset_connection()

    assert src["counts_source"] == "ro"
    # Two source files were indexed, so File nodes >= 2.
    assert src["file_count"] >= 2
    assert isinstance(src["endpoint_count"], int)


# ---------------------------------------------------------------------------
# Tier 3: FTS only
# ---------------------------------------------------------------------------


def test_fts_none_when_no_db(tmp_path):
    # No fts.db on disk -> "none".
    src = _status_via_fts(str(tmp_path))
    assert src["counts_source"] == "none"
    assert src["fts_symbols"] is None


def test_fts_counts_symbols(tmp_path):
    _make_fts(tmp_path, 5)
    src = _status_via_fts(str(tmp_path))
    assert src["counts_source"] == "fts_only"
    assert src["fts_symbols"] == 5
    # FTS tier never touches the graph, so file_count stays 0.
    assert src["file_count"] == 0


def test_fts_fallback_when_ro_open_finds_nothing(tmp_path):
    # Reproduce the cmd_status selection: no graph DB so RO open yields
    # "none", then FTS resolves. Confirms the ladder lands on fts_only.
    _make_fts(tmp_path, 3)
    reset_connection()

    src = _status_via_ro_open(str(tmp_path))
    assert src["counts_source"] == "none"

    src = _status_via_fts(str(tmp_path))
    assert src["counts_source"] == "fts_only"
    assert src["fts_symbols"] == 3
