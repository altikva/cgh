# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI package: shared console, helpers, and logo.

from __future__ import annotations

from rich.console import Console

from codegraph import __version__ as VERSION
from codegraph.core.utils import lang_color as _lang_color
from codegraph.core.utils import rows as _rows
from codegraph.core.utils import short_path as _short_path

console = Console()

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
