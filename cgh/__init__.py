# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Thin entry shim so `python -m cgh` works. The real package is
#              `codegraph` (the import name is intentionally different from the
#              PyPI / CLI name `cgh`, like pillow / PIL). This shim only exists
#              to make the module invocation match the command name. Import
#              actual code from `codegraph`, not from here.

from __future__ import annotations

from codegraph import __version__

__all__ = ["__version__"]
