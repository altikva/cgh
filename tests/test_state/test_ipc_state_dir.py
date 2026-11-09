# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The owner/worker/port markers sit in the resolved state dir, so a
#              repo still on a legacy .codegraph/ keeps its markers beside its
#              data and a new proxy finds the live owner instead of spawning a
#              second one against a held lock.
from __future__ import annotations

from cgh.state import ipc


def test_markers_default_to_cgh_on_a_fresh_repo(tmp_path):
    assert ipc.owner_pidfile(tmp_path) == tmp_path / ".cgh" / "owner.pid"
    assert ipc.port_file(tmp_path) == tmp_path / ".cgh" / "server.port"
    assert ipc.workers_dir(tmp_path) == tmp_path / ".cgh" / "workers"


def test_markers_follow_a_legacy_dir_until_migrated(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    assert ipc.owner_pidfile(tmp_path) == tmp_path / ".codegraph" / "owner.pid"
    assert ipc.port_file(tmp_path) == tmp_path / ".codegraph" / "server.port"
    assert ipc.workers_dir(tmp_path) == tmp_path / ".codegraph" / "workers"


def test_markers_prefer_cgh_once_it_exists(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    (tmp_path / ".cgh").mkdir()
    assert ipc.owner_pidfile(tmp_path) == tmp_path / ".cgh" / "owner.pid"
    assert ipc.port_file(tmp_path) == tmp_path / ".cgh" / "server.port"
