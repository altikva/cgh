# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-21
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The single-writer acquire() must be atomic. A live holder is
#              never clobbered, a stale pidfile is taken over, and under a
#              concurrent-spawn race exactly one owner wins, so the loser can
#              never leave the pidfile pointing at its own dead process while
#              another owner serves.

from __future__ import annotations

import subprocess
import sys

from codegraph.state import pidfile


def test_acquire_on_empty_dir_succeeds(tmp_path):
    acquired, other = pidfile.acquire(tmp_path)
    assert acquired is True
    assert other is None
    assert pidfile.read_existing_pid(tmp_path) == __import__("os").getpid()


def test_acquire_takes_over_a_stale_pidfile(monkeypatch, tmp_path):
    # A crashed owner left a pidfile naming a now-dead pid.
    (tmp_path / ".codegraph").mkdir()
    pidfile._pidfile_path(tmp_path).write_text("999999\n", encoding="utf-8")
    monkeypatch.setattr(pidfile, "_is_process_alive", lambda pid: False)

    acquired, other = pidfile.acquire(tmp_path)
    assert acquired is True
    assert other is None
    assert pidfile.read_existing_pid(tmp_path) == __import__("os").getpid()


def test_acquire_rejects_and_never_clobbers_a_live_holder(monkeypatch, tmp_path):
    (tmp_path / ".codegraph").mkdir()
    pidfile._pidfile_path(tmp_path).write_text("4242\n", encoding="utf-8")
    # 4242 is a different, live owner.
    monkeypatch.setattr(pidfile, "_is_process_alive", lambda pid: pid == 4242)

    acquired, other = pidfile.acquire(tmp_path)
    assert acquired is False
    assert other == 4242
    # The live holder's pid is left untouched.
    assert pidfile.read_existing_pid(tmp_path) == 4242


# A standalone claimant, synchronised by a file barrier so process-startup
# jitter cannot serialise the attempts: announce readiness (drop a file under
# argv[2]), spin until the GO file (argv[3]) appears, then hit acquire() within
# the same tight window. The check-then-write race is microseconds wide, so
# every claimant must reach acquire() together to expose it. A winner then holds
# the slot for HOLD seconds, long enough that a claimant delayed by boot stagger
# still sees a live holder rather than a freed slot, so the test measures
# simultaneous winners (an atomicity failure), not sequential re-acquisition.
_CLAIMANT = (
    "import sys, os, time;"
    "from pathlib import Path;"
    "from codegraph.state.pidfile import acquire;"
    "ready, go = Path(sys.argv[2]), Path(sys.argv[3]);"
    "ready.write_text(str(os.getpid()));"
    "_ = [None for _ in iter(lambda: not go.exists(), False)];"
    "ok, _ = acquire(sys.argv[1]);"
    "sys.stdout.write('WON' if ok else 'LOST'); sys.stdout.flush();"
    "time.sleep(4.0) if ok else None"
)


def test_concurrent_acquire_has_exactly_one_winner(tmp_path):
    # Many claimants hit acquire() together against a fresh slot. The old
    # check-then-write let more than one pass the liveness check and both write
    # their pid; the atomic O_EXCL create must let exactly one win.
    import time

    n = 12
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    go = barrier / "GO"
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _CLAIMANT,
                str(tmp_path),
                str(barrier / f"ready-{i}"),
                str(go),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        for i in range(n)
    ]
    # Release only once every claimant has booted and is spinning on GO, so they
    # all reach acquire() together regardless of how slowly the runner starts
    # each process.
    deadline = time.time() + 25
    while len(list(barrier.glob("ready-*"))) < n and time.time() < deadline:
        time.sleep(0.05)
    go.write_text("go")

    outs = [p.communicate(timeout=30)[0].strip() for p in procs]
    assert outs.count("WON") == 1, outs
    assert outs.count("LOST") == n - 1, outs
