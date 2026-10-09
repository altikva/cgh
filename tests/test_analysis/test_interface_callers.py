# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Callers reached through an interface. A call on an attribute
#              typed with a Protocol or a base class links to the interface's
#              method only; asking for the callers of an implementation
#              (`Class.method`, `module.Class.method`) adds the interface
#              method's callers, marked `via`, and impact counts them too.
#              The CALLS edges themselves stay precise.

from __future__ import annotations

import json
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.analysis.callers import callers_of
from codegraph.analysis.impact import build_impact_report, reverse_calls_bfs
from codegraph.analysis.interfaces import InterfaceResolver, qualified_methods
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_repo

REPO = {
    "app/__init__.py": "",
    "app/ports.py": (
        "import abc\n"
        "from typing import Protocol\n\n\n"
        "class KmsClient(Protocol):\n"
        "    def __enter__(self): ...\n"
        "    def destroy_crypto_key(self, name: str) -> None: ...\n"
        "    def encrypt(self, data: bytes) -> bytes: ...\n\n\n"
        "class Store(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def put(self, key): ...\n"
    ),
    "app/impl.py": (
        "from app.ports import Store\n\n\n"
        "class GoogleKmsClient:\n"
        "    def destroy_crypto_key(self, name: str) -> None:\n"
        "        pass\n\n"
        "    def encrypt(self, data: bytes) -> bytes:\n"
        "        return data\n\n\n"
        "class PartialKms:\n"
        "    def destroy_crypto_key(self, name):\n"
        "        pass\n\n\n"
        "class DiskStore(Store):\n"
        "    def put(self, key):\n"
        "        pass\n"
    ),
    "app/service.py": (
        "from app.impl import GoogleKmsClient\n"
        "from app.ports import KmsClient, Store\n\n\n"
        "class Service:\n"
        "    def __init__(self, kms: KmsClient, store: Store):\n"
        "        self._kms = kms\n"
        "        self._store = store\n\n"
        "    def destroy_org_kek(self, org):\n"
        "        self._kms.destroy_crypto_key(org)\n\n"
        "    def save(self, key):\n"
        "        self._store.put(key)\n\n"
        "    def direct(self, org):\n"
        "        client = GoogleKmsClient()\n"
        "        self._kms.destroy_crypto_key(org)\n"
        "        GoogleKmsClient.destroy_crypto_key(client, org)\n"
    ),
}


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


@pytest.fixture(params=["duckdb", "sqlite"])
def repo(request, tmp_path, monkeypatch):
    if request.param == "duckdb":
        pytest.importorskip("duckdb")
    monkeypatch.setenv("CGH_DB", request.param)
    reset_connection()
    root = tmp_path.resolve() / "repo"
    _write(root, REPO)
    index_repo(root, method="os_walk")
    _srv._root = root
    _srv._conn = None
    yield root
    reset_connection()
    _srv._root = None
    _srv._conn = None


def _rows(root: Path, name: str) -> dict[str, str | None]:
    """{caller: via} of callers_of(name)."""
    return {r["caller"]: r.get("via") for r in callers_of(get_connection(root), name)}


def test_edge_stays_on_the_protocol_method(repo):
    conn = get_connection(repo)
    rows = conn.find_neighbors(
        "CALLS",
        src_where={"name": "destroy_org_kek"},
        return_dst=["id"],
    )
    assert [r["dst_id"].rpartition("::")[2] for r in rows] == [
        "KmsClient.destroy_crypto_key"
    ]


def test_implementation_lists_callers_through_the_protocol(repo):
    rows = _rows(repo, "GoogleKmsClient.destroy_crypto_key")
    assert rows["destroy_org_kek"] == "KmsClient.destroy_crypto_key"
    # A caller reaching the method directly too carries no `via`.
    assert rows["direct"] is None


def test_module_qualified_name_selects_the_definition(repo):
    for name in (
        "impl.GoogleKmsClient.destroy_crypto_key",
        "app.impl.GoogleKmsClient.destroy_crypto_key",
    ):
        assert _rows(repo, name)["destroy_org_kek"] == "KmsClient.destroy_crypto_key"
    assert _rows(repo, "ports.GoogleKmsClient.destroy_crypto_key") == {}
    conn = get_connection(repo)
    assert [
        i.rpartition("::")[2] for i in qualified_methods(conn, "service.Service.save")
    ] == ["Service.save"]


def test_explicit_base_class_counts(repo):
    assert _rows(repo, "DiskStore.put") == {"save": "Store.put"}


def test_partial_implementation_is_not_a_protocol_member(repo):
    # PartialKms lacks encrypt: it does not conform to KmsClient.
    assert _rows(repo, "PartialKms.destroy_crypto_key") == {}


def test_interface_method_itself_has_no_via(repo):
    rows = _rows(repo, "KmsClient.destroy_crypto_key")
    assert rows["destroy_org_kek"] is None


def test_dunders_are_ignored_for_conformance(repo):
    conn = get_connection(repo)
    [impl] = qualified_methods(conn, "GoogleKmsClient.encrypt")
    assert [
        i.rpartition("::")[2] for i in InterfaceResolver(conn).interfaces_of(impl)
    ] == ["KmsClient.encrypt"]


def test_bare_name_is_unchanged(repo):
    rows = callers_of(get_connection(repo), "destroy_crypto_key")
    assert {r["caller"] for r in rows} == {"destroy_org_kek", "direct"}
    assert not any("via" in r for r in rows)


def test_impact_of_the_implementation_file_counts_interface_callers(repo):
    conn = get_connection(repo)
    impl = str(repo / "app/impl.py")
    files, _trunc = reverse_calls_bfs(conn, [impl])
    assert str(repo / "app/service.py") in files
    report = build_impact_report(conn, repo, [impl])
    assert "app/service.py" in json.dumps(report)


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _tools() -> dict:
    from codegraph.server.tools_insight import register as register_insight
    from codegraph.server.tools_query import register as register_query

    m = _FakeMcp()
    register_query(m)
    register_insight(m)
    return m.tools


def test_tools_find_callers_and_impact_of(repo):
    tools = _tools()
    out = json.loads(tools["find_callers"]("GoogleKmsClient.destroy_crypto_key"))
    vias = {c["caller"]: c.get("via") for c in out["callers"]}
    assert vias["destroy_org_kek"] == "KmsClient.destroy_crypto_key"

    by_path = json.loads(tools["impact_of"](str(repo / "app/impl.py")))
    assert "app/service.py" in json.dumps(by_path)
    by_symbol = json.loads(tools["impact_of"]("GoogleKmsClient.destroy_crypto_key"))
    assert "destroy_org_kek" in json.dumps(by_symbol)
