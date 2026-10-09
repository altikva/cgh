# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The DuckDB backend makes a missing pandas fail fast. DuckDB
#              tries to import pandas while binding each parameter; without
#              the sys.modules sentinel every try searches sys.path again.
#              Each case runs in a fresh interpreter so the sentinel never
#              leaks into the test process.

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

_PROBE = (
    "import importlib.util, sys\n"
    "import codegraph.core.db_duckdb\n"
    "entry = sys.modules.get('pandas', 'absent')\n"
    "print('none' if entry is None else 'absent' if entry == 'absent' else 'module')\n"
    "print(importlib.util.find_spec('pandas') is None)\n"
)


def _run(code: str, extra_path: Path | None = None) -> list[str]:
    env = dict(os.environ)
    if extra_path is not None:
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(extra_path), env.get("PYTHONPATH", "")) if p
        )
    out = subprocess.run(  # fixed interpreter, test-owned code
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return out.stdout.split()


def _pandas_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("pandas") is not None


@pytest.mark.skipif(_pandas_installed(), reason="pandas is installed here")
def test_missing_pandas_gets_the_sentinel():
    # find_spec keeps answering None, as optional-dependency checks expect.
    assert _run(_PROBE) == ["none", "True"]


@pytest.mark.skipif(_pandas_installed(), reason="pandas is installed here")
def test_missing_pandas_still_raises_import_error():
    code = (
        "import codegraph.core.db_duckdb\n"
        "try:\n    import pandas\nexcept ImportError:\n    print('import-error')\n"
    )
    assert _run(code) == ["import-error"]


def test_installed_pandas_is_never_shadowed(tmp_path):
    fake = tmp_path / "pandas"
    fake.mkdir()
    (fake / "__init__.py").write_text("MARK = 'fake'\n")
    code = "import codegraph.core.db_duckdb, sys\nimport pandas\nprint(pandas.MARK)\n"
    assert _run(code, tmp_path) == ["fake"]


@pytest.mark.skipif(_pandas_installed(), reason="pandas is installed here")
def test_parameter_binding_does_not_search_for_pandas():
    # Count the path-finder lookups of pandas during parameterized executes.
    code = (
        "import importlib.machinery as m\n"
        "import codegraph.core.db_duckdb, duckdb\n"
        "hits = []\n"
        "orig = m.PathFinder.find_spec\n"
        "def spy(name, *a, **k):\n"
        "    if name == 'pandas':\n"
        "        hits.append(name)\n"
        "    return orig(name, *a, **k)\n"
        "m.PathFinder.find_spec = spy\n"
        "c = duckdb.connect()\n"
        "c.execute('create table t(a varchar, b integer)')\n"
        "for i in range(50):\n"
        "    c.execute('insert into t values (?, ?)', [str(i), i])\n"
        "print(len(hits))\n"
    )
    assert _run(code) == ["0"]
