# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Two threads re-indexing one file at once (the owner's watcher
#              on a save while a reindex touches the same path) must take
#              turns. Unserialised, both delete and insert the same rows and
#              DuckDB refuses one with "Conflict on tuple deletion".

from __future__ import annotations

import json
import subprocess
import sys
import threading

from codegraph.indexer import index_file


def _spec(i: int) -> str:
    paths = {
        f"/items/{n}": {"get": {"summary": f"item {n} rev {i}", "responses": {}}}
        for n in range(400 - (i % 2) * 100)
    }
    return json.dumps(
        {"openapi": "3.1.0", "info": {"title": f"api {i}"}, "paths": paths}, indent=2
    )


def test_concurrent_index_file_on_one_path_takes_turns(tmp_path):
    spec = tmp_path / "openapi.json"
    spec.write_text(_spec(0))
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n")
    # Built by another process, like the owner opening a store an earlier
    # `cgh index` wrote: indexing in this process first hides the collision.
    subprocess.run(
        [sys.executable, "-m", "codegraph", "index", "--root", str(tmp_path)],
        check=True,
        capture_output=True,
    )

    errors: list[str] = []

    def writer() -> None:
        for _ in range(15):
            try:
                index_file(spec, str(tmp_path), force=True)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                return

    def mutator() -> None:
        for i in range(30):
            spec.write_text(_spec(i))

    threads = [threading.Thread(target=writer) for _ in range(2)]
    threads.append(threading.Thread(target=mutator))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
