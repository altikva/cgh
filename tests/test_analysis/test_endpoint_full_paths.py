# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The endpoints query on a real index, both backends. A route's
#              full path is composed from its router prefix and the
#              include_router / register_blueprint calls reaching it, across
#              files and through nested routers; a prefix that is not a
#              literal stops the chain. A full path, a glob or a path with
#              other parameter names finds the route, and a path that matches
#              nothing falls back to a flagged suffix match. Two routers of
#              one file declaring the same method and path stay two routes,
#              and routes declared in test files are left out by default,
#              from the endpoints query, the CLI and impact alike.

from __future__ import annotations

import argparse
import io
import json

import pytest
from rich.console import Console

import codegraph.cli.commands_query as cq
from codegraph.analysis.endpoint_query import list_endpoints, select_endpoints
from codegraph.analysis.impact import endpoints_in_files
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_repo

FILES = {
    "app/__init__.py": "",
    "app/config.py": 'PREFIX = "/dyn"\n',
    "app/main.py": (
        "from fastapi import FastAPI\n\n"
        "from app.routers import api_router\n"
        "from app.routers import health\n\n\n"
        "def create_app():\n"
        "    app = FastAPI()\n"
        "    app.include_router(api_router)\n"
        "    app.include_router(health.router)\n\n"
        '    @app.get("/ping")\n'
        "    def ping():\n"
        '        return "pong"\n\n'
        "    return app\n"
    ),
    "app/routers/__init__.py": (
        "from fastapi import APIRouter\n\n"
        "from app.config import PREFIX\n"
        "from app.routers import email as email_mod\n"
        "from app.routers.donations import router as donations_router\n"
        "from app.routers.dyn import router as dyn_router\n\n"
        'api_router = APIRouter(prefix="/v1")\n'
        "api_router.include_router(donations_router)\n"
        "api_router.include_router(email_mod.router)\n"
        'api_router.include_router(email_mod.router, prefix="/legacy")\n'
        "api_router.include_router(email_mod.images_router)\n"
        "api_router.include_router(dyn_router, prefix=PREFIX)\n"
    ),
    "app/routers/health.py": (
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\n\n\n"
        '@router.get("/health")\n'
        "def health():\n"
        '    return "ok"\n'
    ),
    "app/routers/donations.py": (
        "from fastapi import APIRouter\n\n"
        'router = APIRouter(prefix="/donations")\n\n\n'
        '@router.get("")\n'
        "def list_donations():\n"
        "    return []\n\n\n"
        '@router.post("/{donation_id}/cancel")\n'
        "def cancel_donation(donation_id: int):\n"
        "    return donation_id\n"
    ),
    # Two routers of one file, both declaring GET "".
    "app/routers/email.py": (
        "from fastapi import APIRouter\n\n"
        'router = APIRouter(prefix="/templates")\n'
        'images_router = APIRouter(prefix="/images")\n\n\n'
        '@router.get("")\n'
        "def list_templates():\n"
        "    return []\n\n\n"
        '@images_router.get("")\n'
        "def list_images():\n"
        "    return []\n"
    ),
    "app/routers/dyn.py": (
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\n\n\n"
        '@router.get("/{token}")\n'
        "def resolve(token: str):\n"
        "    return token\n"
    ),
    # Two routers including each other: composition must stop.
    "app/routers/loop.py": (
        "from fastapi import APIRouter\n\n"
        'a = APIRouter(prefix="/a")\n'
        'b = APIRouter(prefix="/b")\n'
        "a.include_router(b)\n"
        "b.include_router(a)\n\n\n"
        '@a.get("/x")\n'
        "def x():\n"
        "    return 1\n"
    ),
    "app/web.py": (
        "from flask import Blueprint, Flask\n\n"
        'bp = Blueprint("web", __name__, url_prefix="/web")\n\n\n'
        '@bp.route("/home")\n'
        "def home():\n"
        '    return "home"\n\n\n'
        "site = Flask(__name__)\n"
        'site.register_blueprint(bp, url_prefix="/site")\n'
    ),
    "tests/__init__.py": "",
    "tests/test_guard.py": (
        "from fastapi import FastAPI\n\n"
        "from app.routers.donations import router as donations_router\n\n"
        "app = FastAPI()\n"
        'app.include_router(donations_router, prefix="/mounted-by-a-test")\n\n\n'
        '@app.post("/v1/donations/{donation_id}/cancel")\n'
        "def fake_cancel(donation_id: int):\n"
        "    return donation_id\n"
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


def _endpoints(root) -> dict[str, dict]:
    """handler name -> endpoint row."""
    return {e["handler"]: e for e in list_endpoints(get_connection(root))}


def _select(root, pattern="", **kw) -> list[dict]:
    payload = select_endpoints(
        [("parent", list_endpoints(get_connection(root)))], pattern, **kw
    )
    return [row for rows in payload["by_framework"].values() for row in rows]


def test_full_paths_compose_router_and_include_prefixes(repo):
    eps = _endpoints(repo)
    assert eps["cancel_donation"]["path"] == "/{donation_id}/cancel"
    assert eps["cancel_donation"]["full_paths"] == [
        "/v1/donations/{donation_id}/cancel"
    ]
    assert eps["list_donations"]["full_paths"] == ["/v1/donations"]
    assert eps["health"]["full_paths"] == ["/health"]
    # A route of an app built in a factory function.
    ping = [e for e in list_endpoints(get_connection(repo)) if e["path"] == "/ping"]
    assert [e["full_paths"] for e in ping] == [["/ping"]]
    assert eps["home"]["full_paths"] == ["/site/home"]


def test_router_included_under_two_prefixes_has_two_full_paths(repo):
    assert sorted(_endpoints(repo)["list_templates"]["full_paths"]) == [
        "/v1/legacy/templates",
        "/v1/templates",
    ]


def test_non_literal_prefix_stops_the_chain(repo):
    dyn = _endpoints(repo)["resolve"]
    assert dyn["full_paths"] == []
    assert dyn["full_path_partial"] is True


def test_include_cycle_terminates(repo):
    assert _endpoints(repo)["x"]["full_paths"] == ["/b/a/x"]


def test_two_routers_of_one_file_with_the_same_route_are_both_kept(repo):
    rows = [
        e
        for e in list_endpoints(get_connection(repo))
        if e["file_path"].endswith("app/routers/email.py")
    ]
    assert sorted((e["method"], e["path"], e["handler"]) for e in rows) == [
        ("GET", "", "list_images"),
        ("GET", "", "list_templates"),
    ]
    assert len({e["id"] for e in rows}) == 2


@pytest.mark.parametrize(
    "pattern",
    [
        "/v1/donations/{donation_id}/cancel",
        "/v1/donations/{id}/cancel",
        "*/donations/{donation_id}/cancel",
        "*/donations/{id}/cancel",
        "/{donation_id}/cancel",
    ],
)
def test_full_path_and_glob_queries_find_the_route(repo, pattern):
    rows = _select(repo, pattern)
    assert [r["handler"] for r in rows] == ["cancel_donation"]
    assert rows[0]["match"] in ("full_path", "path")


def test_unmatched_path_falls_back_to_a_flagged_suffix_match(repo):
    # The real mount is /v1/donations/...: this path has another prefix.
    rows = _select(repo, "/api/v2/donations/{id}/cancel")
    assert [(r["handler"], r["match"]) for r in rows] == [("cancel_donation", "suffix")]


def test_suffix_matches_rank_by_shared_literal_segments():
    eps = [
        {
            "path": "/{a}/attribute/cancel",
            "full_paths": [],
            "file_path": "f",
            "handler": "loose",
        },
        {
            "path": "/{id}/cancel",
            "full_paths": [],
            "file_path": "f",
            "handler": "close",
        },
        {
            "path": "/donations/{id}/cancel",
            "full_paths": [],
            "file_path": "f",
            "handler": "best",
        },
    ]
    payload = select_endpoints([("parent", eps)], "/api/donations/{id}/cancel")
    rows = payload["by_framework"]["unknown"]
    assert [r["handler"] for r in rows] == ["best", "close", "loose"]
    assert {r["match"] for r in rows} == {"suffix"}


def test_generic_local_paths_never_match_by_suffix(repo):
    # dyn's local path is a lone parameter, list_* are "": too generic.
    assert _select(repo, "/nowhere/{anything}") == []


def test_test_routes_are_left_out_by_default(repo):
    rows = _select(repo, "/v1/donations/{donation_id}/cancel")
    assert [r["handler"] for r in rows] == ["cancel_donation"]
    payload = select_endpoints(
        [("parent", list_endpoints(get_connection(repo)))],
        "/v1/donations/{donation_id}/cancel",
    )
    assert payload["tests_excluded"] == 1

    with_tests = _select(repo, "/v1/donations/{donation_id}/cancel", include_tests=True)
    assert sorted(r["handler"] for r in with_tests) == [
        "cancel_donation",
        "fake_cancel",
    ]
    assert [r.get("test") for r in with_tests if r["handler"] == "fake_cancel"] == [
        True
    ]


def test_a_test_mounting_a_real_router_adds_no_full_path(repo):
    assert _endpoints(repo)["cancel_donation"]["full_paths"] == [
        "/v1/donations/{donation_id}/cancel"
    ]


def test_impact_leaves_test_routes_out(repo):
    conn = get_connection(repo)
    assert endpoints_in_files(conn, [str(repo / "tests/test_guard.py")]) == []
    rows = endpoints_in_files(conn, [str(repo / "app/routers/email.py")])
    assert sorted(r["line"] for r in rows) == [7, 12]


@pytest.fixture
def captured_console(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(cq, "console", Console(file=buf, width=300, no_color=True))
    return buf


def _args(root, pattern="", **kw):
    return argparse.Namespace(
        root=str(root),
        pattern=pattern,
        method=kw.get("method", ""),
        include_tests=kw.get("include_tests", False),
        limit=kw.get("limit", 0),
        json=kw.get("json", False),
    )


def test_cli_endpoints_json(repo, capsys):
    cq.cmd_endpoints(_args(repo, "/v1/donations/{id}/cancel", json=True))
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 1
    assert out["tests_excluded"] == 1
    ep = out["endpoints"][0]
    assert ep["full_paths"] == ["/v1/donations/{donation_id}/cancel"]
    assert ep["file"] == "app/routers/donations.py"
    assert ep["line"] == 11


def test_cli_endpoints_text_method_and_limit(repo, captured_console):
    cq.cmd_endpoints(_args(repo, method="post", limit=1))
    text = captured_console.getvalue()
    assert "POST" in text
    assert "/v1/donations/{donation_id}/cancel" in text
    assert "app/routers/donations.py:11" in text
    assert "GET" not in text


def test_cli_endpoints_asks_the_live_owner_first(tmp_path, monkeypatch, capsys):
    import codegraph.cli.owner_client as oc
    from codegraph.cli.owner_client import OwnerReply

    served = {
        "total": 1,
        "tests_excluded": 0,
        "by_framework": {
            "fastapi": [
                {
                    "scope": "parent",
                    "method": "GET",
                    "path": "",
                    "full_paths": ["/v1/x"],
                    "handler": "list_x",
                    "file": "app/x.py",
                    "line": 3,
                }
            ]
        },
    }
    seen = {}

    def _call(_root, tool, arguments):
        seen[tool] = arguments
        return OwnerReply("ok", data=served)

    def _boom(*_a, **_k):
        raise AssertionError("local read-only open must not run")

    monkeypatch.setattr(oc, "call_owner_tool", _call)
    monkeypatch.setattr("codegraph.core.db.get_readonly_connection", _boom)
    cq.cmd_endpoints(_args(tmp_path, "/v1/x", include_tests=True, json=True))
    out = json.loads(capsys.readouterr().out)
    assert [e["handler"] for e in out["endpoints"]] == ["list_x"]
    assert seen["endpoints"] == {
        "path_pattern": "/v1/x",
        "method": "",
        "include_tests": True,
    }
