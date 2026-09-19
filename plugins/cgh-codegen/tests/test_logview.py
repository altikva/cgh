# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-19
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: test codegen_activity, which filters activity_log to codegen
#              events (generated, extended, egress_denied) and supports limit
#              to return the most recent N entries.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_codegen")

from cgh_codegen.logview import codegen_activity

from codegraph.plugin_api import activity_log


def test_keeps_only_codegen_events(tmp_path):
    activity_log(str(tmp_path), "codegen_generated", "a.py <- b.py")
    activity_log(str(tmp_path), "reindex", "noise")
    activity_log(str(tmp_path), "codegen_extended", "c.py extended")

    rows = codegen_activity(tmp_path)

    assert len(rows) == 2
    for row in rows:
        assert row[1] in (
            "codegen_generated",
            "codegen_extended",
            "codegen_egress_denied",
        )
    assert not any(row[1] == "reindex" for row in rows)
    details = [row[2] for row in rows]
    assert "a.py <- b.py" in details
    assert "c.py extended" in details


def test_empty_when_no_codegen_activity(tmp_path):
    activity_log(str(tmp_path), "reindex", "x")

    rows = codegen_activity(tmp_path)

    assert rows == []


def test_limit_returns_most_recent(tmp_path):
    activity_log(str(tmp_path), "codegen_generated", "one")
    activity_log(str(tmp_path), "codegen_generated", "two")
    activity_log(str(tmp_path), "codegen_generated", "three")

    rows = codegen_activity(tmp_path, limit=2)

    assert len(rows) == 2
    assert [r[2] for r in rows] == ["two", "three"]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
