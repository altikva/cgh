"""Owner log rotation — driven by config.log_max_mb / log_backup_count."""

from __future__ import annotations

from pathlib import Path

from codegraph.ipc import rotate_owner_log


def _write_log(path: Path, size_bytes: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size_bytes)


def _write_config(repo: Path, **kv) -> None:
    cfg = repo / ".codegraph" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    body = "[codegraph]\n" + "".join(f"{k} = {v}\n" for k, v in kv.items())
    cfg.write_text(body)


class TestRotation:
    def test_no_rotation_when_under_limit(self, tmp_path):
        log = tmp_path / ".codegraph" / "owner.log"
        _write_log(log, 1024)  # 1 KB, well under 5 MB default
        rotate_owner_log(tmp_path)
        assert log.exists()
        assert log.stat().st_size == 1024
        assert not (tmp_path / ".codegraph" / "owner.log.1").exists()

    def test_rotates_when_over_limit(self, tmp_path):
        _write_config(tmp_path, log_max_mb=1, log_backup_count=2)
        log = tmp_path / ".codegraph" / "owner.log"
        _write_log(log, 2 * 1024 * 1024)  # 2 MB, over the 1 MB cap

        rotate_owner_log(tmp_path)

        assert not log.exists(), "active log should have been moved aside"
        assert (tmp_path / ".codegraph" / "owner.log.1").exists()
        assert (tmp_path / ".codegraph" / "owner.log.1").stat().st_size == 2 * 1024 * 1024

    def test_keeps_only_backup_count_backups(self, tmp_path):
        _write_config(tmp_path, log_max_mb=1, log_backup_count=2)
        cgdir = tmp_path / ".codegraph"
        # Pre-existing backups simulate two prior rotations
        _write_log(cgdir / "owner.log", 2 * 1024 * 1024)
        _write_log(cgdir / "owner.log.1", 2 * 1024 * 1024)
        _write_log(cgdir / "owner.log.2", 2 * 1024 * 1024)

        rotate_owner_log(tmp_path)

        # owner.log -> .1, .1 -> .2, old .2 dropped
        assert not (cgdir / "owner.log").exists()
        assert (cgdir / "owner.log.1").exists()
        assert (cgdir / "owner.log.2").exists()
        assert not (cgdir / "owner.log.3").exists()

    def test_backup_count_zero_just_deletes(self, tmp_path):
        _write_config(tmp_path, log_max_mb=1, log_backup_count=0)
        log = tmp_path / ".codegraph" / "owner.log"
        _write_log(log, 2 * 1024 * 1024)

        rotate_owner_log(tmp_path)

        assert not log.exists()
        assert not (tmp_path / ".codegraph" / "owner.log.1").exists()

    def test_max_mb_zero_disables_rotation(self, tmp_path):
        _write_config(tmp_path, log_max_mb=0, log_backup_count=3)
        log = tmp_path / ".codegraph" / "owner.log"
        _write_log(log, 50 * 1024 * 1024)  # 50 MB

        rotate_owner_log(tmp_path)

        assert log.exists()
        assert log.stat().st_size == 50 * 1024 * 1024

    def test_no_op_when_log_missing(self, tmp_path):
        # Should not raise
        rotate_owner_log(tmp_path)
        assert not (tmp_path / ".codegraph" / "owner.log").exists()
