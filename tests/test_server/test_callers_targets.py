# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A name matching several definitions (a method and its test
#              double) gives one row per caller naming the definitions, not
#              the same caller twice.

from __future__ import annotations

from codegraph.analysis.callers import callers_of


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def find_neighbors(self, *_a, **_k):
        return self._rows


def _row(caller, file, line, dst):
    return {
        "src_name": caller,
        "src_file_path": file,
        "src_start_line": line,
        "dst_file_path": dst,
    }


def test_one_row_per_caller_with_targets_when_ambiguous():
    rows = [
        _row("revoke", "app/h.py", 649, "tests/test_h.py"),
        _row("revoke", "app/h.py", 649, "app/m.py"),
        _row("issue", "app/h.py", 700, "app/m.py"),
    ]
    assert callers_of(_Conn(rows), "release_ref") == [
        {
            "caller": "revoke",
            "file": "app/h.py",
            "line": 649,
            "targets": ["app/m.py", "tests/test_h.py"],
        },
        {"caller": "issue", "file": "app/h.py", "line": 700, "targets": ["app/m.py"]},
    ]


def test_no_targets_when_the_name_is_unique():
    rows = [_row("revoke", "app/h.py", 649, "app/m.py")]
    assert callers_of(_Conn(rows), "release_ref") == [
        {"caller": "revoke", "file": "app/h.py", "line": 649}
    ]
