"""Tests for codegraph.core.fts — BM25 full-text search."""

import pytest

from codegraph.core.fts import (
    commit,
    delete_file_symbols,
    fts_search,
    get_fts_conn,
    upsert_symbol,
)


@pytest.fixture
def fts_conn(tmp_path):
    """Create an in-memory-like FTS connection in tmp dir."""
    conn = get_fts_conn(tmp_path)
    yield conn


class TestFTS:
    def test_upsert_and_search(self, fts_conn):
        upsert_symbol(
            fts_conn,
            sym_id="sample.py::validate",
            kind="function",
            name="validate",
            file_path="sample.py",
            start_line=10,
            end_line=15,
            docstring="Validate input data",
        )
        commit(fts_conn)

        results = fts_search(fts_conn, "validate")
        assert len(results) > 0
        assert results[0].name == "validate"

    def test_search_by_docstring(self, fts_conn):
        upsert_symbol(
            fts_conn,
            sym_id="api.py::DonationHandler",
            kind="class",
            name="DonationHandler",
            file_path="api.py",
            start_line=1,
            end_line=50,
            docstring="Handles donation creation and payment processing",
        )
        commit(fts_conn)

        results = fts_search(fts_conn, "payment processing")
        assert len(results) > 0
        assert any(r.name == "DonationHandler" for r in results)

    def test_search_no_results(self, fts_conn):
        results = fts_search(fts_conn, "nonexistent_xyz_123")
        assert results == []

    def test_delete_file_symbols(self, fts_conn):
        upsert_symbol(
            fts_conn,
            sym_id="delete_me.py::func_a",
            kind="function",
            name="func_a",
            file_path="delete_me.py",
            start_line=1,
            end_line=5,
            docstring="To be deleted",
        )
        commit(fts_conn)

        # Verify it exists
        results = fts_search(fts_conn, "func_a")
        assert len(results) > 0

        # Delete and verify gone
        delete_file_symbols(fts_conn, "delete_me.py")
        commit(fts_conn)
        results = fts_search(fts_conn, "func_a")
        assert len(results) == 0

    def test_upsert_updates_existing(self, fts_conn):
        upsert_symbol(
            fts_conn,
            sym_id="file.py::my_func",
            kind="function",
            name="my_func",
            file_path="file.py",
            start_line=1,
            end_line=5,
            docstring="Version 1",
        )
        commit(fts_conn)

        upsert_symbol(
            fts_conn,
            sym_id="file.py::my_func",
            kind="function",
            name="my_func",
            file_path="file.py",
            start_line=1,
            end_line=10,
            docstring="Version 2 updated",
        )
        commit(fts_conn)

        results = fts_search(fts_conn, "my_func")
        assert len(results) == 1
