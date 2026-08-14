# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-14
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh stop` is a discoverable top-level alias for
#              `cgh serve --stop`. It must go through the exact same
#              teardown: unregister the caller's worker and the keepalive
#              marker, then stop the owner. Here we assert the delegation
#              (stop flag forced on, worker + keepalive unregistered) and
#              that the verb is wired into the argparse dispatch.

from __future__ import annotations

import argparse

import codegraph.state.ipc as ipc
from codegraph.cli.commands_index import cmd_stop


def test_cmd_stop_runs_the_serve_stop_teardown(tmp_path, monkeypatch):
    (tmp_path / ".codegraph").mkdir()
    calls: dict[str, object] = {}
    # cmd_serve imports these from codegraph.state.ipc at call time, so
    # patching the module attributes is enough to observe the teardown.
    monkeypatch.setattr(
        ipc, "unregister_worker", lambda root: calls.setdefault("worker", root)
    )
    monkeypatch.setattr(
        ipc, "unregister_keepalive", lambda root: calls.setdefault("keepalive", root)
    )

    args = argparse.Namespace(root=str(tmp_path))
    cmd_stop(args)

    # Delegated as `serve --stop`.
    assert args.stop is True
    # No owner in a bare repo, but the worker + keepalive teardown still ran.
    assert "worker" in calls and "keepalive" in calls


def test_stop_verb_is_registered(tmp_path):
    from codegraph.__main__ import _LogoArgumentParser, _register_setup_and_serve

    ap = _LogoArgumentParser(prog="codegraph", add_help=False)
    sub = ap.add_subparsers(dest="cmd", parser_class=_LogoArgumentParser)
    _register_setup_and_serve(sub)
    args = ap.parse_args(["stop", "--root", str(tmp_path)])
    assert args.cmd == "stop"
