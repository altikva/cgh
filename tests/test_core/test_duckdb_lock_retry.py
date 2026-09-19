"""
Tests for codegraph.core.db_duckdb._connect_with_retry, which retries a
DuckDB open when a concurrent writer holds the file lock.
"""

from __future__ import annotations

import duckdb
import pytest

import codegraph.core.db_duckdb as db_duckdb
from codegraph.core.db_duckdb import _connect_with_retry

# ---- tests -----------------------------------------------------------------


def test_retries_a_lock_error_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake(path, read_only=False):
        calls["n"] += 1
        if calls["n"] < 3:
            raise duckdb.IOException("Could not set lock on file graph.duckdb")
        return "CONN"

    monkeypatch.setattr(db_duckdb.duckdb, "connect", fake)
    result = _connect_with_retry("x", False, wait_seconds=5, _sleep=lambda d: None)
    assert result == "CONN"
    assert calls["n"] == 3


def test_reraises_when_budget_exhausted(monkeypatch):
    def fake(path, read_only=False):
        raise duckdb.IOException("Could not set lock")

    monkeypatch.setattr(db_duckdb.duckdb, "connect", fake)
    with pytest.raises(duckdb.IOException):
        _connect_with_retry("x", False, wait_seconds=0, _sleep=lambda d: None)


def test_non_lock_error_is_not_retried(monkeypatch):
    calls = {"n": 0}

    def fake(path, read_only=False):
        calls["n"] += 1
        raise duckdb.IOException("disk is full")

    monkeypatch.setattr(db_duckdb.duckdb, "connect", fake)
    with pytest.raises(duckdb.IOException):
        _connect_with_retry("x", False, wait_seconds=5, _sleep=lambda d: None)
    assert calls["n"] == 1
