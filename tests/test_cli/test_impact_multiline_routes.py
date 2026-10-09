# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh impact` on an edit inside a service method reaches the
#              routes declared with a decorator spanning several lines or
#              giving its path as path=, through their handler, and lists a
#              test that sits under tests/unit/handlers/ among the tests to
#              run rather than as an impacted handler.

from __future__ import annotations

import pytest

from codegraph.analysis.impact import build_impact_report
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_repo

SERVICE = 'class Cipher:\n    def ensure_key(self) -> str:\n        return "k"\n'

FILES = {
    "app/__init__.py": "",
    "app/services/__init__.py": "",
    "app/services/cipher.py": SERVICE,
    "app/handlers/__init__.py": "",
    "app/handlers/upload_handler.py": (
        "from app.services.cipher import Cipher\n\n\n"
        "def store() -> str:\n"
        "    return Cipher().ensure_key()\n"
    ),
    "app/routers/__init__.py": "",
    "app/routers/docs.py": (
        "from fastapi import APIRouter\n\n"
        "from app.handlers.upload_handler import store\n\n"
        'router = APIRouter(prefix="/docs")\n\n\n'
        "@router.post(\n"
        '    "/{doc_id}/upload",\n'
        "    status_code=201,\n"
        ")\n"
        "def upload(doc_id: int) -> str:\n"
        "    return store()\n\n\n"
        '@router.put(path="/{doc_id}", status_code=200)\n'
        "def replace(doc_id: int) -> str:\n"
        "    return store()\n\n\n"
        '@router.get("/{doc_id}")\n'
        "def read(doc_id: int) -> str:\n"
        '    return "x"\n'
    ),
    "tests/__init__.py": "",
    "tests/unit/__init__.py": "",
    "tests/unit/handlers/__init__.py": "",
    "tests/unit/handlers/test_upload_handler.py": (
        "from app.handlers.upload_handler import store\n\n\n"
        "def test_store():\n"
        '    assert store() == "k"\n'
    ),
}


@pytest.fixture(params=["duckdb", "sqlite"])
def repo(tmp_path, request, monkeypatch):
    if request.param == "duckdb":
        pytest.importorskip("duckdb")
    monkeypatch.setenv("CGH_DB", request.param)
    root = tmp_path.resolve()
    for rel, body in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    reset_connection()
    index_repo(str(root))
    yield root
    reset_connection()


def test_edit_in_a_service_reaches_multi_line_and_keyword_routes(repo):
    line = 1 + SERVICE.splitlines().index('        return "k"')
    report = build_impact_report(
        get_connection(repo), str(repo), [f"app/services/cipher.py#L{line}"]
    )
    assert [s["name"] for s in report["changed_symbols"]] == ["ensure_key"]
    assert sorted((e["method"], e["path"]) for e in report["endpoints"]) == [
        ("POST", "/{doc_id}/upload"),
        ("PUT", "/{doc_id}"),
    ]
    tests = [t["file"] for t in report["tests_to_run"]]
    assert tests == ["tests/unit/handlers/test_upload_handler.py"]
    roles = {r["file"]: r["role"] for r in report["impacted"]}
    assert roles["tests/unit/handlers/test_upload_handler.py"] == "test"
