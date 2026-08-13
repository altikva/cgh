# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Deferred scan errors collapse by message: the same error over
#              many files logs one summary line with a count, while distinct
#              errors each get their own line. Guards against the per-file
#              flood (a misconfigured summarize model 404-ing on every file).

from __future__ import annotations

import logging

from codegraph.state import deferred_scan


def test_identical_errors_collapse_to_one_line(monkeypatch, caplog):
    def _boom(repo_root, path, blob_sha):
        if path.endswith("d.py"):
            raise RuntimeError("a different error")
        raise RuntimeError("summarize backend ollama: HTTP Error 404")

    monkeypatch.setattr(deferred_scan, "_process", _boom)

    with caplog.at_level(logging.ERROR, logger="codegraph.state.deferred_scan"):
        for name in ("a.py", "b.py", "c.py", "d.py"):
            deferred_scan.enqueue("/repo", f"/repo/{name}", "sha")
        deferred_scan.drain_for_tests()

    lines = [r.getMessage() for r in caplog.records]
    # One summary for the 3 identical 404s, one for the distinct error.
    flood = [ln for ln in lines if "HTTP Error 404" in ln]
    other = [ln for ln in lines if "a different error" in ln]
    assert len(flood) == 1, f"expected one collapsed 404 line, got: {flood}"
    assert "x3" in flood[0]
    assert len(other) == 1


def test_single_error_logs_once_without_count(monkeypatch, caplog):
    monkeypatch.setattr(
        deferred_scan,
        "_process",
        lambda r, p, s: (_ for _ in ()).throw(RuntimeError("one off")),
    )
    with caplog.at_level(logging.ERROR, logger="codegraph.state.deferred_scan"):
        deferred_scan.enqueue("/repo", "/repo/only.py", "sha")
        deferred_scan.drain_for_tests()
    lines = [r.getMessage() for r in caplog.records if "one off" in r.getMessage()]
    assert len(lines) == 1 and "x" not in lines[0].split("one off")[0][-3:]
