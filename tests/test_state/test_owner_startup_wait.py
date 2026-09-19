# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-19
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: _await_owner_port must return the port when the owner is alive, return
#              None after the startup timeout expires, and keep the timeout budget well
#              below the client handshake budget.

from __future__ import annotations

from codegraph.state.ipc import (
    _CLIENT_HANDSHAKE_BUDGET,
    _OWNER_STARTUP_TIMEOUT,
    _await_owner_port,
)


def test_returns_port_when_owner_is_alive():
    r = _await_owner_port(
        "x",
        20.0,
        _alive=lambda root: True,
        _read_port=lambda root: 4242,
        _now=lambda: 0.0,
        _sleep=lambda d: None,
    )
    assert r == 4242


def test_returns_none_after_timeout():
    clock = {"t": 0.0}

    def now():
        return clock["t"]

    def sleep(d):
        clock["t"] += d

    r = _await_owner_port(
        "x",
        20.0,
        _alive=lambda root: False,
        _read_port=lambda root: 1,
        _now=now,
        _sleep=sleep,
    )
    assert r is None
    assert clock["t"] <= 20.25


def test_startup_timeout_under_client_budget():
    assert _OWNER_STARTUP_TIMEOUT < _CLIENT_HANDSHAKE_BUDGET
