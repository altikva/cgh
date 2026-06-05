# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the `python -m cgh` entry shim: it runs the real CLI
#              and its version mirrors codegraph, while codegraph stays the
#              canonical import name.

from __future__ import annotations

import subprocess
import sys


def test_python_m_cgh_runs_and_shows_version():
    out = subprocess.run(
        [sys.executable, "-m", "cgh", "--version"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0
    # The banner + "codegraph X.Y.Z" line both go to stdout.
    assert "codegraph" in out.stdout


def test_cgh_shim_version_matches_codegraph():
    import cgh
    import codegraph

    assert cgh.__version__ == codegraph.__version__


def test_codegraph_is_still_the_real_package():
    # The shim must not shadow the real package: actual modules live under
    # codegraph, not cgh.
    import codegraph.indexer  # noqa: F401
    from codegraph.__main__ import main  # noqa: F401
