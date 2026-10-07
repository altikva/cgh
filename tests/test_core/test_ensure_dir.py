# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-06
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: ensure_dir survives a drive whose mkdir says "already exists"
#              before its stat cache agrees (WebDAV mounts on Windows).

from __future__ import annotations

from pathlib import Path

import pytest

from codegraph.core import utils
from codegraph.core.utils import ensure_dir


def test_creates_missing_parents(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b"
    ensure_dir(target)
    ensure_dir(target)  # idempotent
    assert target.is_dir()


def test_retries_when_mkdir_reports_exists_too_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "dav"
    calls = {"n": 0}
    real_mkdir = Path.mkdir

    def flaky_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            # The redirector answers "exists" while stat still says absent.
            raise FileExistsError(str(self))
        real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", flaky_mkdir)
    monkeypatch.setattr(utils.time, "sleep", lambda _s: None)
    ensure_dir(target)
    assert target.is_dir() and calls["n"] == 2


def test_a_file_in_the_way_still_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "taken"
    target.write_text("x", encoding="utf-8")
    monkeypatch.setattr(utils.time, "sleep", lambda _s: None)
    with pytest.raises(FileExistsError):
        ensure_dir(target)
