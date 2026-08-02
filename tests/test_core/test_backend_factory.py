# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The backend factory (detect_backend_file is the single
#              tie-break authority, open_graphdb_file_ro the shared RO
#              opener) and the identifier allow-list: any
#              non-identifier-shaped field name raises BackendError
#              before it can reach a query string.

from __future__ import annotations

import pytest

from codegraph.core import db as core_db
from codegraph.core.utils import checked_identifier
from codegraph.errors import BackendError


@pytest.fixture(autouse=True)
def clean_conns():
    core_db.reset_connection()
    yield
    core_db.reset_connection()


def _repo(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    return tmp_path


class TestDetectBackendFile:
    def test_none_when_empty(self, tmp_path):
        assert core_db.detect_backend_file(_repo(tmp_path)) is None

    def test_duckdb_detected(self, tmp_path):
        root = _repo(tmp_path)
        (root / ".codegraph" / "graph.duckdb").write_bytes(b"")
        backend, path = core_db.detect_backend_file(root)
        assert backend == "duckdb" and path.name == "graph.duckdb"

    def test_duckdb_wins_the_tie(self, tmp_path):
        root = _repo(tmp_path)
        (root / ".codegraph" / "graph.duckdb").write_bytes(b"")
        (root / ".codegraph" / "graph.db").write_bytes(b"")
        assert core_db.detect_backend_file(root)[0] == "duckdb"

    def test_kuzu_alone_detected(self, tmp_path):
        root = _repo(tmp_path)
        (root / ".codegraph" / "graph.db").write_bytes(b"")
        assert core_db.detect_backend_file(root)[0] == "kuzu"


class TestOpenGraphdbFileRo:
    def test_opens_a_real_duckdb_file(self, tmp_path):
        root = _repo(tmp_path)
        rw = core_db.get_connection(root)  # creates graph.duckdb
        rw.count_nodes("File")
        core_db.reset_connection(root)
        backend, path = core_db.detect_backend_file(root)
        conn = core_db.open_graphdb_file_ro(backend, path)
        assert conn is not None
        assert conn.count_nodes("File") == 0
        conn.close()

    def test_degrades_to_none_on_garbage(self, tmp_path):
        root = _repo(tmp_path)
        bad = root / ".codegraph" / "graph.duckdb"
        bad.write_bytes(b"not a duckdb file")
        assert core_db.open_graphdb_file_ro("duckdb", bad) is None


class TestIdentifierAllowList:
    def test_identifier_shapes_pass(self):
        assert checked_identifier("path") == "path"
        assert checked_identifier("start_line") == "start_line"
        assert checked_identifier("_private") == "_private"

    @pytest.mark.parametrize(
        "bad",
        ["path; DROP TABLE file", "a b", "name'", "", "1st", 'x"', "*"],
    )
    def test_injection_shapes_raise(self, bad):
        with pytest.raises(BackendError):
            checked_identifier(bad)

    def test_find_nodes_rejects_hostile_field(self, tmp_path):
        root = _repo(tmp_path)
        conn = core_db.get_connection(root)
        with pytest.raises(BackendError):
            conn.find_nodes("File", where={"path); DROP TABLE file;--": "x"})
        with pytest.raises(BackendError):
            conn.find_nodes("File", return_fields=["path, (SELECT 1)"])
        with pytest.raises(BackendError):
            conn.find_nodes("File", order_by=["path; --"])
