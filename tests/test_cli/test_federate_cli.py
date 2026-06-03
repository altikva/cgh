"""
CLI-level tests for `cgh federate`.

The federation model layer (ChildStatus.has_graphdb) is covered in
tests/test_server/test_federation.py. These tests pin the CLI rendering
on top of it — specifically that a DuckDB-only subrepo is accepted by
`federate add` and shown as OK by the status table, rather than wrongly
reported as "graph.db missing" (the v0.4 regression where the CLI gated
on has_kuzu instead of has_graphdb).
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

import pytest
from rich.console import Console

import codegraph.cli.commands_federate as fed


def _mk_subrepo(root: Path, *, duckdb: bool) -> Path:
    cg = root / ".codegraph"
    cg.mkdir(parents=True, exist_ok=True)
    (cg / ("graph.duckdb" if duckdb else "graph.db")).write_bytes(b"fake")
    (cg / "fts.db").write_bytes(b"fake")
    return root


@pytest.fixture
def captured_console(monkeypatch):
    """Replace the module console with one writing to a StringIO buffer."""
    buf = io.StringIO()
    monkeypatch.setattr(fed, "console", Console(file=buf, width=200, no_color=True))
    return buf


class TestFederateAdd:
    def test_add_duckdb_subrepo_succeeds(self, tmp_path, captured_console):
        parent = tmp_path / "parent"
        parent.mkdir()
        (parent / ".codegraph").mkdir()
        child = _mk_subrepo(tmp_path / "child", duckdb=True)

        fed._cmd_add(argparse.Namespace(root=str(parent), paths=[str(child)]))

        out = captured_console.getvalue()
        assert "federated" in out
        assert "graph.db missing" not in out
        assert "no graph DB" not in out

    def test_add_kuzu_subrepo_still_succeeds(self, tmp_path, captured_console):
        parent = tmp_path / "parent"
        parent.mkdir()
        (parent / ".codegraph").mkdir()
        child = _mk_subrepo(tmp_path / "child", duckdb=False)

        fed._cmd_add(argparse.Namespace(root=str(parent), paths=[str(child)]))

        assert "federated" in captured_console.getvalue()

    def test_add_subrepo_without_graphdb_warns(self, tmp_path, captured_console):
        parent = tmp_path / "parent"
        parent.mkdir()
        (parent / ".codegraph").mkdir()
        child = tmp_path / "child"
        (child / ".codegraph").mkdir(parents=True)  # initialized, but no DB

        fed._cmd_add(argparse.Namespace(root=str(parent), paths=[str(child)]))

        out = captured_console.getvalue()
        assert "no graph DB" in out
        assert "federated" not in out


class TestFederateStatusBadge:
    def test_duckdb_subrepo_renders_ok_with_backend(self, tmp_path, captured_console):
        parent = tmp_path / "parent"
        parent.mkdir()
        child = _mk_subrepo(parent / "child", duckdb=True)

        fed._render_status_table(parent, [child])

        out = captured_console.getvalue()
        assert "ok" in out
        assert "duckdb" in out
        assert "no graph" not in out
