# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Free-text (prose) search over the FTS store: stopword removal
#              in English and French, OR-of-terms bm25 ranking, FTS5 syntax
#              escaping, and the `cgh search --text` CLI path.

from __future__ import annotations

import argparse
import json
import sqlite3

import pytest

from codegraph.cli.commands_query import cmd_search
from codegraph.core.fts import (
    commit,
    fts_search,
    get_fts_conn,
    prose_match_query,
    prose_terms,
    upsert_symbol,
)

_DOCS = [
    (
        "resume",
        "md_section",
        "Reprise de session",
        "docs/MEMORY.md",
        "Après un /clear, le serveur recharge le dernier checkpoint de la "
        "session et le résumé du transcript.",
    ),
    (
        "auth",
        "function",
        "verify_token",
        "app/auth.py",
        "Validate the bearer token and reject an expired signature.",
    ),
    (
        "user",
        "function",
        "delete_account",
        "app/users.py",
        "Supprime le compte de l'utilisateur et ses données personnelles.",
    ),
    (
        "noise",
        "function",
        "render_chart",
        "app/charts.py",
        "Draw the bar chart for the dashboard.",
    ),
]


@pytest.fixture
def repo(tmp_path):
    conn = get_fts_conn(tmp_path)
    for i, (sid, kind, name, path, doc) in enumerate(_DOCS):
        upsert_symbol(conn, sid, kind, name, str(tmp_path / path), 10 * i + 1, 0, doc)
    commit(conn)
    conn.close()
    return tmp_path


def _search(root, text, json_out=True, limit=None):
    return argparse.Namespace(
        root=str(root), query=None, text=text, limit=limit, offset=0, json=json_out
    )


class TestProseTerms:
    def test_french_stopwords_and_elision_dropped(self):
        terms = prose_terms(
            "Comment le serveur gère la reprise de session après un clear"
        )
        assert terms == ["serveur", "gère", "reprise", "session", "clear"]

    def test_apostrophe_splits_elided_article(self):
        assert prose_terms("l'utilisateur") == ["utilisateur"]
        assert prose_terms("l’utilisateur d’abord") == [
            "utilisateur",
            "abord",
        ]

    def test_stopwords_match_without_accents(self):
        # "être" and "etre", "à" and "a" are all stopwords.
        assert prose_terms("être etre à a été") == []

    def test_english_stopwords_dropped(self):
        assert prose_terms("how does the token get verified") == [
            "token",
            "verified",
        ]

    def test_camel_case_adds_parts(self):
        assert prose_terms("GoTrueClient") == ["gotrueclient", "go", "true", "client"]

    def test_match_query_quotes_every_term(self):
        q = prose_match_query('NEAR(a b) OR "x" title:secret -bad*')
        assert q == '"near" OR "title" OR "secret" OR "bad"'


class TestProseSearch:
    def test_french_prose_finds_the_section(self, repo):
        conn = get_fts_conn(repo)
        hits = fts_search(
            conn,
            "comment le serveur gère la reprise de session après un clear",
            prose=True,
        )
        assert hits and hits[0].name == "Reprise de session"
        assert hits[0].score > 0

    def test_english_prose_finds_the_function(self, repo):
        conn = get_fts_conn(repo)
        hits = fts_search(
            conn, "where do we reject an expired token?", limit=5, prose=True
        )
        assert hits[0].name == "verify_token"

    def test_elided_word_matches(self, repo):
        conn = get_fts_conn(repo)
        hits = fts_search(conn, "les données de l'utilisateur", prose=True)
        assert hits[0].name == "delete_account"

    @pytest.mark.parametrize(
        "text",
        ['"', "(", "a AND", "NEAR(", "col:", "*", '"unterminated', "^x", "-", ""],
    )
    def test_fts5_syntax_does_not_raise(self, repo, text):
        conn = get_fts_conn(repo)
        assert isinstance(fts_search(conn, text, prose=True), list)
        assert isinstance(fts_search(conn, text), list)

    def test_default_mode_retries_a_sentence_as_prose(self, repo):
        # As written, the sentence is an implicit AND of every word and
        # matches nothing; the default mode (the MCP fts_search tool) now
        # retries it as free text.
        conn = get_fts_conn(repo)
        hits = fts_search(conn, "comment le serveur gère la reprise de session")
        assert hits and hits[0].name == "Reprise de session"

    def test_default_mode_single_word_unchanged(self, repo):
        conn = get_fts_conn(repo)
        hits = fts_search(conn, "verify_token")
        assert hits[0].name == "verify_token"


class TestSearchTextCli:
    def test_json_shape(self, repo, capsys):
        cmd_search(_search(repo, "les données de l'utilisateur"))
        out = json.loads(capsys.readouterr().out)
        assert out["terms"] == ["données", "utilisateur"]
        first = out["results"][0]
        assert set(first) == {
            "scope",
            "file",
            "line",
            "symbol",
            "kind",
            "snippet",
            "score",
        }
        assert first["symbol"] == "delete_account"
        assert first["file"].endswith("app/users.py")
        assert first["line"] == 21
        assert first["scope"] == "parent"
        assert "utilisateur" in first["snippet"]

    def test_limit(self, repo, capsys):
        cmd_search(_search(repo, "token chart session compte", limit=2))
        out = json.loads(capsys.readouterr().out)
        assert out["returned"] == 2

    def test_only_stopwords_returns_empty(self, repo, capsys):
        cmd_search(_search(repo, "le la de the of"))
        out = json.loads(capsys.readouterr().out)
        assert out["terms"] == [] and out["results"] == []

    def test_query_and_text_together_is_an_error(self, repo):
        args = _search(repo, "token")
        args.query = "verify"
        with pytest.raises(SystemExit):
            cmd_search(args)

    def test_works_beside_a_writer_holding_the_fts_and_graph(self, repo, capsys):
        # An owner keeps a write connection on fts.db (WAL) and holds the
        # graph DB's exclusive lock. The text search reads fts.db read-only
        # and never opens the graph, so neither blocks it.
        duckdb = pytest.importorskip("duckdb")
        graph = duckdb.connect(str(repo / ".codegraph" / "graph.duckdb"))
        writer = sqlite3.connect(str(repo / ".codegraph" / "fts.db"))
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO symbols(sym_id, kind, name, file_path, start_line) "
            "VALUES ('pending', 'function', 'pending_token', 'x.py', 1)"
        )
        try:
            cmd_search(_search(repo, "expired token"))
            out = json.loads(capsys.readouterr().out)
        finally:
            writer.rollback()
            writer.close()
            graph.close()
        assert out["results"][0]["symbol"] == "verify_token"
        assert "warnings" not in out

    def test_human_output(self, repo, capsys):
        cmd_search(_search(repo, "reprise de session", json_out=False))
        out = capsys.readouterr().out
        assert "Reprise de session" in out
        assert "terms: reprise session" in out
