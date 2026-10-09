# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Edges resolved by name other than CALLS (class bases, markdown
#              mentions and links, endpoint handlers in another file) are
#              kept as name references, so they are found whatever order the
#              files are indexed in and survive a reindex of the target file.
#              Covers the handler matching rule (only files the route file
#              imports), the purge of a file's references and the one-time
#              re-parse of an index written before references were stored.
#              Run on both backends.

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_file, index_repo
from codegraph.state.scan_meta import GRAPH_FORMAT, read_meta


@pytest.fixture(params=["duckdb", "sqlite"])
def backend(request, monkeypatch):
    if request.param == "duckdb":
        pytest.importorskip("duckdb")
    monkeypatch.setenv("CGH_DB", request.param)
    reset_connection()
    yield request.param
    reset_connection()


URLS = (
    "from django.urls import path\n\n"
    "from pkg import views\n\n"
    'urlpatterns = [path("users/<int:pk>/", views.user_detail)]\n'
)
URLS_RELATIVE = (
    "from django.urls import path\n\n"
    "from .views import user_detail\n\n"
    'urlpatterns = [path("users/<int:pk>/", user_detail)]\n'
)
VIEWS = "def user_detail(request, pk):\n    return pk\n"
ENDPOINT = "pkg/urls.py::5::ANY::/users/<int:pk>/"


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


def _index(root: Path, order: list[str], force: bool = False) -> None:
    for rel in order:
        assert index_file(root / rel, root, force=force)


def _rows(root: Path, sql: str) -> set[tuple]:
    res = get_connection(root).execute(sql)
    pre = f"{root}/"
    out = set()
    while res.has_next():
        out.add(tuple(v.replace(pre, "") for v in res.get_next()))
    return out


def _handlers(root: Path) -> set[tuple]:
    return _rows(root, "SELECT from_id, to_id FROM edge_implemented_by")


@pytest.mark.parametrize("urls", [URLS, URLS_RELATIVE], ids=["module", "relative"])
@pytest.mark.parametrize("views_first", [True, False])
def test_handler_in_imported_module_is_linked_in_any_order(
    tmp_path, backend, urls, views_first
):
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/urls.py": urls,
            "pkg/views.py": VIEWS,
            # Same name, never imported by urls.py: must not be linked.
            "other/views.py": VIEWS,
        },
    )
    order = ["pkg/__init__.py", "pkg/urls.py", "other/views.py"]
    order.insert(0 if views_first else 3, "pkg/views.py")
    _index(tmp_path, order)
    assert _handlers(tmp_path) == {(ENDPOINT, "pkg/views.py::user_detail")}

    # Saving the view's file purges the edge into it; it comes back.
    _index(tmp_path, ["pkg/views.py"], force=True)
    assert _handlers(tmp_path) == {(ENDPOINT, "pkg/views.py::user_detail")}


def test_handler_in_route_file_wins(tmp_path, backend):
    local = URLS + "\n\ndef user_detail(request, pk):\n    return pk\n"
    _write(
        tmp_path, {"pkg/__init__.py": "", "pkg/urls.py": local, "pkg/views.py": VIEWS}
    )
    _index(tmp_path, ["pkg/views.py", "pkg/__init__.py", "pkg/urls.py"])
    assert _handlers(tmp_path) == {(ENDPOINT, "pkg/urls.py::user_detail")}


def test_handler_without_import_is_not_guessed(tmp_path, backend):
    lone = 'from django.urls import path\n\nurlpatterns = [path("users/<int:pk>/", views.user_detail)]\n'
    _write(tmp_path, {"pkg/urls.py": lone, "pkg/views.py": VIEWS})
    _index(tmp_path, ["pkg/urls.py", "pkg/views.py"])
    assert _handlers(tmp_path) == set()


def test_purge_drops_the_files_references(tmp_path, backend):
    _write(
        tmp_path,
        {
            "doc.md": "# Doc\n\n`BaseModel` lives in [base](base.py).\n",
            "base.py": "class BaseModel:\n    pass\n",
            "sub.py": "from base import BaseModel\n\n\nclass Sub(BaseModel):\n    pass\n",
        },
    )
    _index(tmp_path, ["doc.md", "sub.py", "base.py"])
    assert _rows(tmp_path, "SELECT from_id, to_id FROM edge_inherits") == {
        ("sub.py::Sub", "base.py::BaseModel")
    }
    assert _rows(tmp_path, "SELECT to_path FROM edge_md_links_to") == {("base.py",)}
    assert _rows(tmp_path, "SELECT to_id FROM edge_md_refs_class") == {
        ("base.py::BaseModel",)
    }
    conn = get_connection(tmp_path)
    conn.delete_file_completely(str(tmp_path / "doc.md"))
    conn.delete_file_completely(str(tmp_path / "sub.py"))
    assert _rows(tmp_path, "SELECT file_path FROM name_ref") == set()
    _index(tmp_path, ["base.py"], force=True)
    assert _rows(tmp_path, "SELECT from_id FROM edge_inherits") == set()
    assert _rows(tmp_path, "SELECT from_id FROM edge_md_refs_class") == set()


def test_index_from_before_name_refs_reparses_once(tmp_path, backend):
    _write(
        tmp_path,
        {
            "base.py": "class BaseModel:\n    pass\n",
            "sub.py": "from base import BaseModel\n\n\nclass Sub(BaseModel):\n    pass\n",
        },
    )
    index_repo(tmp_path, method="os_walk")
    # What a format 2 index looks like after the base's file was saved: no
    # stored references and the inbound INHERITS edge lost.
    conn = get_connection(tmp_path)
    conn.execute("DELETE FROM name_ref")
    conn.execute("DELETE FROM edge_inherits")
    meta_path = tmp_path / ".codegraph" / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["graph_format"] = GRAPH_FORMAT - 1
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    index_repo(tmp_path, method="os_walk")
    assert _rows(tmp_path, "SELECT from_id, to_id FROM edge_inherits") == {
        ("sub.py::Sub", "base.py::BaseModel")
    }
    assert read_meta(tmp_path)["graph_format"] == GRAPH_FORMAT


def test_deleting_a_file_drops_links_into_it(tmp_path, backend):
    """A deleted file leaves no markdown link pointing at it, and a link
    comes back when the file does."""
    _write(
        tmp_path,
        {"guide.md": "# Guide\n\nSee [the api](api.md).\n", "api.md": "# API\n"},
    )
    _index(tmp_path, ["guide.md", "api.md"])
    links = "SELECT from_id, to_path FROM edge_md_links_to"
    assert any(to == "api.md" for _f, to in _rows(tmp_path, links))

    get_connection(tmp_path).delete_file_completely(str(tmp_path / "api.md"))
    assert not any(to == "api.md" for _f, to in _rows(tmp_path, links))

    _index(tmp_path, ["api.md"], force=True)
    assert any(to == "api.md" for _f, to in _rows(tmp_path, links))
