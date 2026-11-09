# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `python -m cgh` runs the real CLI through cgh/__main__.py. The
#              package, import name and CLI are all cgh.

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
    # The banner + "cgh X.Y.Z  context graph hub" line both go to stdout.
    assert "context graph hub" in out.stdout


def test_cgh_package_is_importable():
    import cgh

    assert cgh.__version__
    import cgh.indexer
    from cgh.__main__ import main  # noqa: F401
