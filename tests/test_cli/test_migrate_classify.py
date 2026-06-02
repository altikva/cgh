"""
Tests for the stale-Kuzu classifier in commands_migrate._classify_diff.

The classifier decides whether a count delta between Kuzu and DuckDB
should be tolerated (Kuzu is stale, DuckDB is canonical) or aborted
(real divergence, keep both files for inspection).
"""

from __future__ import annotations

from codegraph.cli.commands_migrate import _classify_diff


class TestStaleKuzuSignatures:
    def test_no_diffs_is_match(self):
        is_stale, reason = _classify_diff([])
        assert is_stale is True
        assert "no diffs" in reason

    def test_pure_shrinkage_is_stale(self):
        # Every metric shrinks — explained by ghost rows from deleted files.
        diffs = [
            ("nodes.File", 470, 452),
            ("nodes.Function", 1115, 1109),
            ("edges.CALLS", 461, 239),
        ]
        is_stale, reason = _classify_diff(diffs)
        assert is_stale is True
        assert "ghost" in reason

    def test_imports_gain_with_shrinkage_is_stale(self):
        # The wb-frontend case: Kuzu predates the IMPORTS fix (0 -> N)
        # plus carries ghost rows from deleted files.
        diffs = [
            ("nodes.File", 470, 452),
            ("nodes.Function", 1115, 1109),
            ("edges.IMPORTS", 0, 18),
            ("edges.CALLS", 461, 239),
        ]
        is_stale, reason = _classify_diff(diffs)
        assert is_stale is True
        assert "IMPORTS" in reason
        assert "ghost" in reason

    def test_imports_gain_alone_is_stale(self):
        # Edge case: Kuzu was indexed pre-fix on an unchanged file set,
        # so the only diff is the IMPORTS edges DuckDB now emits.
        diffs = [("edges.IMPORTS", 0, 18)]
        is_stale, reason = _classify_diff(diffs)
        assert is_stale is True
        assert "IMPORTS" in reason


class TestRealMismatchSignatures:
    def test_extra_functions_in_duckdb_is_mismatch(self):
        # DuckDB found Functions Kuzu didn't — that's a real new-data
        # signal we shouldn't silently throw away.
        diffs = [("nodes.Function", 1115, 1200)]
        is_stale, reason = _classify_diff(diffs)
        assert is_stale is False
        assert "Function" in reason

    def test_extra_calls_in_duckdb_is_mismatch(self):
        # If DuckDB grew CALLS edges, that's not the IMPORTS-fix
        # signature — could be a real parser change that needs review.
        diffs = [("edges.CALLS", 461, 600)]
        is_stale, reason = _classify_diff(diffs)
        assert is_stale is False
        assert "CALLS" in reason

    def test_imports_gain_from_non_zero_is_mismatch(self):
        # If Kuzu already had some IMPORTS edges, the diff isn't the
        # "0 -> N post-fix" signature any more — bail out.
        diffs = [("edges.IMPORTS", 5, 18)]
        is_stale, reason = _classify_diff(diffs)
        assert is_stale is False
        assert "IMPORTS" in reason

    def test_mixed_explained_and_unexplained_is_mismatch(self):
        # Even one unexplained gain is enough to abort, no matter how
        # many other diffs are explainable.
        diffs = [
            ("edges.IMPORTS", 0, 18),  # explained
            ("nodes.File", 470, 452),  # explained (shrink)
            ("nodes.Function", 1115, 1200),  # NOT explained
        ]
        is_stale, reason = _classify_diff(diffs)
        assert is_stale is False
        assert "Function" in reason
