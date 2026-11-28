# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-28
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A state dir cgh may not look into (the global ~/.cgh/, a repo's
#              .cgh/, a shared knowledge store) reads as an absent one on every
#              supported Python: defaults, no siblings, no shared hits, no
#              exception. Path.exists() raises PermissionError there up to
#              Python 3.13, so the denial is simulated at os.stat for both
#              errnos (EACCES from a mode, EPERM from a sandbox) and checked
#              once against a real mode 000 directory.
from __future__ import annotations

import errno
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from cgh.analysis.refs import declared_global_siblings
from cgh.core import config
from cgh.state import call_log

ERRNOS = pytest.mark.parametrize(
    "code", [errno.EACCES, errno.EPERM], ids=["EACCES", "EPERM"]
)

GLOBAL_TOML = '[cgh]\nsiblings = ["../other"]\nmax_file_size_kb = 7\n'


def _deny(monkeypatch: pytest.MonkeyPatch, denied: Path, code: int) -> None:
    """Make every stat of ``denied`` or of a path under it raise
    PermissionError(code), the way a parent directory that refuses the lookup
    does. Both ``Path.exists()`` and ``os.path.exists()`` go through os.stat."""
    real_stat = os.stat
    prefix = str(denied)

    def stat(path, *args, **kwargs):
        text = os.fspath(path) if not isinstance(path, int) else ""
        if isinstance(text, bytes):
            text = os.fsdecode(text)
        if text == prefix or text.startswith(prefix + os.sep):
            raise PermissionError(code, os.strerror(code), text)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat)


def _global_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A redirected global dir holding a config whose values differ from the
    defaults, so "ignored" and "read" cannot be confused."""
    home = tmp_path / "home"
    global_dir = home / ".cgh"
    global_dir.mkdir(parents=True)
    (global_dir / "config.toml").write_text(GLOBAL_TOML, encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_DIR", global_dir)
    return global_dir


def _shared_store(global_dir: Path, name: str) -> Path:
    """A shared knowledge store with one live entry matching "lock"."""
    db = global_dir / "shared" / name / "knowledge.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE knowledge (id INTEGER PRIMARY KEY, kind TEXT, title TEXT,"
        " body TEXT, tags TEXT, file_refs TEXT, session_id TEXT, ts REAL,"
        " superseded_by INTEGER)"
    )
    conn.execute(
        "INSERT INTO knowledge (kind, title, body, tags, file_refs, session_id, ts)"
        " VALUES ('gotcha', 'lock held', 'body', '', '', '', 1.0)"
    )
    conn.commit()
    conn.close()
    return db


def test_the_fixtures_are_read_when_nothing_is_denied(tmp_path, monkeypatch):
    """The control: without a denial the same files ARE read, so the tests
    below prove the denial is what empties the answers."""
    global_dir = _global_dir(tmp_path, monkeypatch)
    db = _shared_store(global_dir, "fleet")
    repo = tmp_path / "repo"
    repo.mkdir()

    assert config.load_config(repo).max_file_size_kb == 7
    assert declared_global_siblings() == ["../other"]
    assert [h["title"] for h in call_log.knowledge_search_ro(db, "lock")] == [
        "lock held"
    ]


@ERRNOS
def test_unreadable_global_dir_reads_as_absent(tmp_path, monkeypatch, code):
    global_dir = _global_dir(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    default = config.CghConfig(project_root=repo).max_file_size_kb
    _deny(monkeypatch, global_dir, code)

    assert config.global_state_dir() == global_dir
    assert config._read_toml(config.global_config_path()) == {}
    assert config.load_config(repo).max_file_size_kb == default != 7
    assert declared_global_siblings() == []


@ERRNOS
def test_unreadable_global_dir_does_not_fall_back_to_a_denied_legacy_dir(
    tmp_path, monkeypatch, code
):
    """With the whole home denied, the legacy ~/.codegraph/ probe is denied
    too: the answer is the default ~/.cgh/, not an exception."""
    global_dir = _global_dir(tmp_path, monkeypatch)
    (global_dir.parent / ".codegraph").mkdir()
    _deny(monkeypatch, global_dir.parent, code)

    assert config.global_state_dir() == global_dir


@ERRNOS
def test_unreadable_repo_state_dir_reads_as_uninitialized(tmp_path, monkeypatch, code):
    _global_dir(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    state = repo / ".cgh"
    state.mkdir(parents=True)
    (state / "config.toml").write_text(
        "[cgh]\nmax_file_size_kb = 9\n", encoding="utf-8"
    )
    sub = repo / "src"
    sub.mkdir()
    assert config.load_config(repo).max_file_size_kb == 9
    assert config.find_cgh_root(sub) == repo
    _deny(monkeypatch, state, code)

    assert config.state_dir_for(repo) == state
    cfg = config.load_config(repo)
    # The project value is gone, the global one (7) still applies.
    assert cfg.max_file_size_kb == 7
    assert cfg.is_initialized is False
    assert config.find_cgh_root(sub) is None


@ERRNOS
def test_unreadable_shared_store_gives_no_hits(tmp_path, monkeypatch, code):
    global_dir = _global_dir(tmp_path, monkeypatch)
    _shared_store(global_dir, "fleet")
    db = call_log.shared_store_path("fleet")
    _deny(monkeypatch, global_dir, code)

    assert call_log.knowledge_search_ro(db, "lock") == []
    assert call_log.knowledge_symptom_search_ro(db, "lock held") == []


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="a mode 000 directory denies nothing on Windows or to root",
)
def test_global_dir_in_mode_000_reads_as_absent(tmp_path, monkeypatch):
    """The real thing, no simulation: EACCES from the directory's mode."""
    global_dir = _global_dir(tmp_path, monkeypatch)
    _shared_store(global_dir, "fleet")
    db = call_log.shared_store_path("fleet")
    repo = tmp_path / "repo"
    repo.mkdir()
    default = config.CghConfig(project_root=repo).max_file_size_kb
    global_dir.chmod(0o000)
    try:
        assert config.load_config(repo).max_file_size_kb == default
        assert declared_global_siblings() == []
        assert call_log.knowledge_search_ro(db, "lock") == []
        assert call_log.knowledge_symptom_search_ro(db, "lock held") == []
    finally:
        global_dir.chmod(0o700)
