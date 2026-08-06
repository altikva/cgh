# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI package: shared console, helpers, and logo.

from __future__ import annotations

import os
from contextlib import contextmanager

from rich.console import Console

from codegraph import __version__ as VERSION
from codegraph.core.utils import lang_color as _lang_color
from codegraph.core.utils import rows as _rows
from codegraph.core.utils import short_path as _short_path

console = Console()

_PROGRESS_CONSOLE: Console | None = None


def show_progress() -> bool:
    """Whether to animate spinners / progress bars.

    Off when a parent asked for quiet via CGH_NO_PROGRESS=1 (a federated
    child run with captured output, a git hook, a background owner): those
    must never spray ANSI into a pipe or a log. On otherwise, including on
    terminals rich mis-detects as non-tty, notably git-bash / mintty on
    Windows (isatty is False there even though a human is watching), which
    is why we also trust MSYSTEM.
    """
    if os.environ.get("CGH_NO_PROGRESS") == "1":
        return False
    return console.is_terminal or bool(os.environ.get("MSYSTEM"))


def progress_console() -> Console:
    """A console that actually renders live progress even where isatty is
    unreliable (git-bash). Only used when show_progress() is True."""
    global _PROGRESS_CONSOLE
    if _PROGRESS_CONSOLE is None:
        _PROGRESS_CONSOLE = Console(force_terminal=True)
    return _PROGRESS_CONSOLE


@contextmanager
def status(message: str):
    """A spinner during a quiet phase, or a silent no-op when progress is
    disabled (background / captured). Safe to wrap any blocking step."""
    if show_progress():
        with progress_console().status(message, spinner="dots"):
            yield
    else:
        yield


LOGO = r"""[bold cyan]
               _                            _
  ___ ___   __| | ___  __ _ _ __ __ _ _ __ | |__
 / __/ _ \ / _` |/ _ \/ _` | '__/ _` | '_ \| '_ \
| (_| (_) | (_| |  __/ (_| | | | (_| | |_) | | | |
 \___\___/ \__,_|\___|\__, |_|  \__,_| .__/|_| |_|
                      |___/          |_|
[/bold cyan]"""


def _get_conn(root, readonly=False):
    """
    Get a Kuzu connection. If readonly=True and DB is locked,
    returns None instead of crashing, caller must handle.
    """
    if readonly:
        from codegraph.core.db import get_readonly_connection

        return get_readonly_connection(root)
    from codegraph.core.db import get_connection

    return get_connection(root)


__all__ = [
    "LOGO",
    "VERSION",
    "_get_conn",
    "_lang_color",
    "_rows",
    "_short_path",
    "console",
]
