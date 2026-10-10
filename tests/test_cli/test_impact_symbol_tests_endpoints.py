# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: For an edit inside one method, `cgh impact` lists the tests and
#              the routes that reach that method through the caller walk.
#              The blast radius was already symbol-precise, but the tests
#              stayed every test importing the changed file and the endpoints
#              every route of an impacted router file. A change at module
#              level keeps the whole-file answer.

from __future__ import annotations

import pytest

from codegraph.analysis.impact import build_impact_report
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_repo

MANAGER = (
    "class DonationManager:\n"
    "    def cancel(self) -> int:\n"
    "        return 1\n\n"
    "    def validate(self) -> int:\n"
    "        return 2\n"
)

FILES = {
    "app/__init__.py": "",
    "app/manager.py": MANAGER,
    "app/routes.py": (
        "from fastapi import APIRouter\n\n"
        "from app.manager import DonationManager\n\n"
        "router = APIRouter()\n\n\n"
        '@router.post("/donations/cancel")\n'
        "def cancel_donation() -> int:\n"
        "    return DonationManager().cancel()\n\n\n"
        '@router.post("/donations/validate")\n'
        "def validate_donation() -> int:\n"
        "    return DonationManager().validate()\n"
    ),
    "tests/__init__.py": "",
    "tests/test_cancel.py": (
        "from app.routes import cancel_donation\n\n\n"
        "def test_cancel():\n"
        "    assert cancel_donation() == 1\n"
    ),
    "tests/test_validate.py": (
        "from app.manager import DonationManager\n\n\n"
        "def test_validate():\n"
        "    assert DonationManager().validate() == 2\n"
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


def _report(root, entry):
    return build_impact_report(get_connection(root), str(root), [entry])


def test_edit_inside_a_method_lists_the_routes_and_tests_reaching_it(repo):
    line = 1 + MANAGER.splitlines().index("        return 1")
    report = _report(repo, f"app/manager.py#L{line}")
    assert [s["name"] for s in report["changed_symbols"]] == ["cancel"]
    assert [(e["method"], e["path"]) for e in report["endpoints"]] == [
        ("POST", "/donations/cancel")
    ]
    assert [t["file"] for t in report["tests_to_run"]] == ["tests/test_cancel.py"]


def test_module_level_change_keeps_every_route_and_importing_test(repo):
    report = _report(repo, "app/manager.py")
    assert {e["path"] for e in report["endpoints"]} == {
        "/donations/cancel",
        "/donations/validate",
    }
    assert {t["file"] for t in report["tests_to_run"]} == {
        "tests/test_cancel.py",
        "tests/test_validate.py",
    }
