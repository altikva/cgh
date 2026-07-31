# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Connection caches are keyed by repo root: two repos in
#              one process get their own graph and knowledge
#              connections (the old first-caller-wins globals handed
#              repo A's DB to repo B), per-root reset only touches its
#              repo, and call_log gains the reset_for_tests hook it
#              lacked.

from __future__ import annotations

import pytest

from codegraph.core import db as core_db
from codegraph.state import call_log


@pytest.fixture(autouse=True)
def clean_conns():
    core_db.reset_connection()
    call_log.reset_for_tests()
    yield
    core_db.reset_connection()
    call_log.reset_for_tests()


def _repo(tmp_path, name):
    root = tmp_path / name
    (root / ".codegraph").mkdir(parents=True)
    return root


class TestGraphConnCache:
    def test_two_repos_get_distinct_connections(self, tmp_path):
        a, b = _repo(tmp_path, "a"), _repo(tmp_path, "b")
        conn_a = core_db.get_connection(a)
        conn_b = core_db.get_connection(b)
        assert conn_a is not conn_b
        # Cache hits stay per-repo.
        assert core_db.get_connection(a) is conn_a
        assert core_db.get_connection(b) is conn_b

    def test_per_root_reset_only_touches_its_repo(self, tmp_path):
        a, b = _repo(tmp_path, "a"), _repo(tmp_path, "b")
        conn_a = core_db.get_connection(a)
        conn_b = core_db.get_connection(b)
        core_db.reset_connection(a)
        assert core_db.get_connection(b) is conn_b
        assert core_db.get_connection(a) is not conn_a

    def test_readonly_reuses_rw_per_repo(self, tmp_path):
        a = _repo(tmp_path, "a")
        rw = core_db.get_connection(a)
        assert core_db.get_readonly_connection(a) is rw


class TestCallLogCache:
    def test_two_repos_get_distinct_knowledge_dbs(self, tmp_path):
        a, b = _repo(tmp_path, "a"), _repo(tmp_path, "b")
        ka = call_log.knowledge_record(
            "only in a", "body", kind="note", tags="", repo_root=a
        )
        assert ka
        titles_b = [e["title"] for e in call_log.knowledge_list(repo_root=b, limit=10)]
        assert "only in a" not in titles_b
        titles_a = [e["title"] for e in call_log.knowledge_list(repo_root=a, limit=10)]
        assert "only in a" in titles_a
        assert (a / ".codegraph" / "call_log.db").exists()
        assert (b / ".codegraph" / "call_log.db").exists()
