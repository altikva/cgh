# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the SQL DDL parser: CREATE TABLE -> table sections with
#              column previews, ALTER TABLE ADD COLUMN folding, malformed input.

from __future__ import annotations

from codegraph.parsers import get_parser, is_supported
from codegraph.parsers.base import FileIndex


def _section(idx, title):
    return next((s for s in idx.sections if s.title == title), None)


SCHEMA = """
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.donations (
    id BIGINT PRIMARY KEY,
    amount NUMERIC(10, 2) NOT NULL,
    user_id BIGINT REFERENCES users(id),
    CONSTRAINT positive_amount CHECK (amount > 0)
);

ALTER TABLE users ADD COLUMN last_login TIMESTAMP;
ALTER TABLE users ADD verified BOOLEAN DEFAULT false;
"""


class TestSqlParser:
    def test_parser_exists(self):
        p = get_parser(".sql")
        assert p is not None
        assert p.lang == "sql"

    def test_tables_become_sections(self, tmp_path):
        f = tmp_path / "schema.sql"
        f.write_text(SCHEMA, encoding="utf-8")
        idx = get_parser(".sql").parse(f)
        assert isinstance(idx, FileIndex)
        titles = {s.title for s in idx.sections}
        assert "table:users" in titles
        assert "table:donations" in titles  # schema prefix stripped

    def test_columns_in_preview(self, tmp_path):
        f = tmp_path / "schema.sql"
        f.write_text(SCHEMA, encoding="utf-8")
        idx = get_parser(".sql").parse(f)
        users = _section(idx, "table:users")
        assert "id" in users.body_preview
        assert "email" in users.body_preview
        assert "created_at" in users.body_preview
        # NUMERIC(10, 2) must not split into a phantom column
        donations = _section(idx, "table:donations")
        assert "amount" in donations.body_preview
        assert "user_id" in donations.body_preview
        # table-level CONSTRAINT is not a column
        assert "positive_amount" not in donations.body_preview

    def test_alter_add_column_folds_in(self, tmp_path):
        f = tmp_path / "schema.sql"
        f.write_text(SCHEMA, encoding="utf-8")
        idx = get_parser(".sql").parse(f)
        users = _section(idx, "table:users")
        assert "last_login" in users.body_preview  # ADD COLUMN
        assert "verified" in users.body_preview  # ADD (no COLUMN keyword)

    def test_line_numbers(self, tmp_path):
        f = tmp_path / "schema.sql"
        f.write_text(SCHEMA, encoding="utf-8")
        idx = get_parser(".sql").parse(f)
        for s in idx.sections:
            assert s.start_line > 0
            assert s.id.startswith(str(f))

    def test_malformed_no_raise(self, tmp_path):
        f = tmp_path / "broken.sql"
        f.write_text("CREATE TABLE oops (\n  id INT", encoding="utf-8")
        idx = get_parser(".sql").parse(f)
        assert isinstance(idx, FileIndex)
        # unterminated paren still yields the table with best-effort columns
        assert any(s.title == "table:oops" for s in idx.sections)


def test_sql_supported():
    assert is_supported("schema.sql")
