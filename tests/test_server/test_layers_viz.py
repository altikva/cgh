# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Test for the layer-dependency diagram (FEAT-12). Indexes a
#              presentation file that imports a domain file, then checks the
#              `layers` scope of visualize_graph emits a layer->layer edge.

from __future__ import annotations

import json

import pytest

import codegraph.server as _srv
from codegraph.core.db import reset_connection
from codegraph.indexer import index_file
from codegraph.server.tools_viz import register as register_viz


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def viz_repo(tmp_path):
    reset_connection()
    _srv._root = tmp_path.resolve()
    _srv._conn = None
    yield tmp_path.resolve()
    reset_connection()
    _srv._root = None
    _srv._conn = None


def test_layers_scope_emits_layer_edge(viz_repo):
    root = viz_repo
    # roles.classify: /routers/ -> presentation, /models/ -> domain.
    routers = root / "routers"
    models = root / "models"
    routers.mkdir()
    models.mkdir()
    (models / "user.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    # A presentation file importing a domain file. The import resolver keys
    # off the module name, so use a top-level module to keep resolution simple.
    (root / "user.py").write_text("def m():\n    return 1\n", encoding="utf-8")
    (routers / "api.py").write_text(
        "import user\n\ndef route():\n    return 1\n", encoding="utf-8"
    )
    index_file(root / "user.py", root)
    index_file(routers / "api.py", root)

    m = _FakeMcp()
    register_viz(m)
    out = json.loads(m.tools["visualize_graph"](scope="layers"))

    assert out["scope"] == "layers"
    diagram = out["diagram"]
    # presentation node and an edge are present (api.py imports user.py).
    assert "presentation" in diagram
    assert "-->" in diagram


def test_layers_scope_dot_format(viz_repo):
    root = viz_repo
    (root / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (root / "app.py").write_text(
        "import lib\n\ndef run():\n    return 1\n", encoding="utf-8"
    )
    index_file(root / "lib.py", root)
    index_file(root / "app.py", root)

    m = _FakeMcp()
    register_viz(m)
    out = json.loads(m.tools["visualize_graph"](scope="layers", format="dot"))
    assert out["diagram"].startswith("digraph layers")
