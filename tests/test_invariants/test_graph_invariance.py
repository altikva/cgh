# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Graph invariance property. The edges of an index must not
#              depend on the order files were ingested in, nor on files
#              being re-saved one by one afterwards. Three graphs are built
#              per backend from the same tree:
#                A  full index_repo, files in sorted order
#                B  full index_repo, files reversed, then seeded shuffle
#                C  graph A after every file is touched and force-reindexed,
#                   one by one, in reverse sorted order
#              and every edge table is compared row for row (ids made
#              relative to the repo root). Each edge kind is its own test id
#              so a regression names the edge kind it breaks.

from __future__ import annotations

import random
import shutil
from pathlib import Path

import pytest

from codegraph.core.db import get_connection, reset_connection
from codegraph.core.graph_model import EDGES
from codegraph.indexer import index_file, index_repo

BACKENDS = ["duckdb", "sqlite"]
SEED = 1337

# ---------------------------------------------------------------------------
# Fixture repo: every name-resolved edge kind, across files
# ---------------------------------------------------------------------------

FIXTURE: dict[str, str] = {
    # One doc at the root (walked before every subdirectory) and one in a
    # subdirectory walked last, so some doc is ingested before the code it
    # references and some after.
    "aa_overview.md": (
        "# Overview\n\n"
        "Call `normalize_key` before `UserRepo` saves. See\n"
        "[the repos](pkg/repos.py) and [notes](zz_docs/notes.md).\n\n"
        "## Widgets\n\n"
        "```python\nrender_total(base_widget)\n```\n"
    ),
    "zz_docs/notes.md": (
        "# Notes\n\n"
        "`list_users` is served by `fetch_users`; `AdminRepo` extends\n"
        "`UserRepo`. Back to [overview](../aa_overview.md).\n\n"
        "## Terraform\n\nSee [main](../infra/main.tf).\n"
    ),
    "pkg/__init__.py": "",
    "pkg/base.py": (
        "from pkg.zz_helpers import normalize_key\n\n\n"
        "class BaseRepo:\n"
        "    def save(self, key):\n"
        "        return normalize_key(key)\n"
    ),
    "pkg/repos.py": (
        "from pkg.base import BaseRepo\n"
        "from pkg.zz_helpers import normalize_key, shared_util\n\n\n"
        "class UserRepo(BaseRepo):\n"
        "    def load(self, key):\n"
        "        return normalize_key(key)\n\n\n"
        "class AdminRepo(UserRepo):\n"
        "    def promote(self):\n"
        "        return shared_util()\n"
    ),
    "pkg/zz_helpers.py": (
        "def normalize_key(key):\n"
        "    return str(key).lower()\n\n\n"
        "def shared_util():\n"
        "    return normalize_key('x')\n"
    ),
    "pkg/api.py": (
        "from fastapi import APIRouter\n\n"
        "from pkg.zz_helpers import shared_util\n\n"
        "router = APIRouter()\n\n\n"
        '@router.get("/users")\n'
        "def list_users():\n"
        "    return fetch_users()\n\n\n"
        '@router.post("/users")\n'
        "async def create_user():\n"
        "    return shared_util()\n\n\n"
        "def fetch_users():\n"
        "    return shared_util()\n"
    ),
    # Django: the route lives in urls.py, the handler in views.py.
    "pkg/urls.py": (
        "from django.urls import path\n\n"
        "from pkg import views\n\n"
        'urlpatterns = [path("users/<int:pk>/", views.user_detail)]\n'
    ),
    "pkg/views.py": (
        "from pkg.api import fetch_users\n\n\n"
        "def user_detail(request, pk):\n"
        "    return fetch_users()\n"
    ),
    "web/src/format.ts": (
        "export function formatAmount(n: number): string {\n"
        "  return padLeft(String(n))\n"
        "}\n\n"
        "export function padLeft(s: string): string {\n"
        "  return ' ' + s\n"
        "}\n"
    ),
    "web/src/widgets.ts": (
        "export class BaseWidget {\n"
        "  draw(): string {\n"
        "    return padLeft('w')\n"
        "  }\n"
        "}\n"
    ),
    "web/src/client.ts": (
        "import { formatAmount, padLeft } from './format'\n"
        "import { BaseWidget } from './widgets'\n\n"
        "export function renderTotal(n: number): string {\n"
        "  return formatAmount(n) + padLeft('!')\n"
        "}\n\n"
        "export class TotalWidget extends BaseWidget {\n"
        "  draw(): string {\n"
        "    return renderTotal(1)\n"
        "  }\n"
        "}\n"
    ),
    # TypeScript inheritance: a generic base defined in a file walked after
    # its subclass, an implemented interface, and this / super calls that
    # resolve through the base. Drawer.stash shares the method name, so only
    # the base tells the two apart.
    "web/src/aa_cart.ts": (
        "import { Store } from './zz_store'\n\n"
        "export interface Saveable {\n  persist(): string\n}\n\n"
        "export class CartStore extends Store<number> implements Saveable {\n"
        "  persist(): string {\n"
        "    return this.stash(1) + super.stash(2)\n"
        "  }\n"
        "}\n"
    ),
    "web/src/zz_store.ts": (
        "export class Store<T> {\n"
        "  stash(item: T): string {\n"
        "    return String(item)\n"
        "  }\n"
        "}\n\n"
        "export class Drawer {\n"
        "  stash(item: string): string {\n"
        "    return item\n"
        "  }\n"
        "}\n"
    ),
    "web/server/users.controller.ts": (
        "import { Controller, Get } from '@nestjs/common'\n"
        "import { renderTotal } from '../src/client'\n\n"
        "@Controller('users')\n"
        "export class UsersController {\n"
        "  @Get('total')\n"
        "  total(): string {\n"
        "    return renderTotal(2)\n"
        "  }\n"
        "}\n"
    ),
    # Terraform: references across the files of one module directory, a
    # local module (inputs and outputs), a tfvars assignment.
    "infra/main.tf": (
        'variable "region" {\n  default = "eu"\n}\n\n'
        'resource "google_storage_bucket" "data" {\n'
        "  location = var.region\n"
        "  name     = local.bucket_name\n"
        "}\n\n"
        'module "net" {\n  source = "./modules/net"\n  region = var.region\n}\n\n'
        'resource "google_compute_instance" "vm" {\n'
        "  network    = module.net.network_id\n"
        "  project    = data.google_project.p.project_id\n"
        "  depends_on = [google_storage_bucket.data]\n"
        "}\n"
    ),
    "infra/locals.tf": (
        'locals {\n  bucket_name = "b-${var.region}"\n}\n\n'
        'data "google_project" "p" {}\n'
    ),
    "infra/outputs.tf": (
        'output "bucket" {\n  value = google_storage_bucket.data.url\n}\n\n'
        'output "network" {\n  value = module.net.network_id\n}\n'
    ),
    "infra/terraform.tfvars": 'region = "eu"\n',
    "infra/modules/net/variables.tf": 'variable "region" {}\n',
    "infra/modules/net/main.tf": (
        'resource "google_compute_network" "n" {\n  name = "n-${var.region}"\n}\n'
    ),
    "infra/modules/net/outputs.tf": (
        'output "network_id" {\n  value = google_compute_network.n.id\n}\n'
    ),
}

# Edge kinds the fixture must populate, or the comparison proves nothing.
EXPECTED_NONEMPTY = sorted(EDGES)


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


def _norm(value, root: Path):
    if isinstance(value, str):
        return value.replace(f"{root}/", "").replace(str(root), "<root>")
    return value


def snapshot_edges(root: Path) -> dict[str, frozenset[tuple]]:
    """Every row of every edge table, ids made relative to ``root``."""
    conn = get_connection(root)
    out: dict[str, frozenset[tuple]] = {}
    for kind, spec in EDGES.items():
        res = conn.execute(f"SELECT * FROM {spec.table}")
        rows: set[tuple] = set()
        while res.has_next():
            rows.add(tuple(_norm(v, root) for v in res.get_next()))
        out[kind] = frozenset(rows)
    return out


def _ordered(how: str):
    """A _filter_parseable replacement that fixes the ingestion order."""
    import codegraph.indexer as indexer

    original = indexer._filter_parseable

    def _filter(candidates, scan_cfg, stats):
        files = sorted(original(candidates, scan_cfg, stats))
        if how == "reversed":
            files.reverse()
        elif how == "shuffled":
            random.Random(SEED).shuffle(files)  # noqa: S311  # test order, not crypto
        return files

    return _filter


def build_graphs(
    src_files: dict[str, str] | None,
    src_tree: Path | None,
    base: Path,
    monkeypatch: pytest.MonkeyPatch,
    resave_sample: int | None = None,
) -> dict[str, dict[str, frozenset[tuple]]]:
    """Build the graphs of the module description under ``base``.

    Returns {"A", "B-reversed", "B-shuffled", "C"} -> edge snapshot. A walks
    files in sorted order so the comparison does not hang on the order a
    filesystem lists directories in; B-reversed flips every pair of files.
    ``src_files`` writes a fixture; ``src_tree`` copies an existing tree.
    ``resave_sample`` limits graph C to the first that many re-saved files.
    """
    import codegraph.indexer as indexer

    out: dict[str, dict[str, frozenset[tuple]]] = {}
    reset_connection()
    for name, how in (
        ("A", "sorted"),
        ("B-reversed", "reversed"),
        ("B-shuffled", "shuffled"),
    ):
        root = base / name
        if src_files is not None:
            _write(root, src_files)
        else:
            shutil.copytree(src_tree, root)
        with monkeypatch.context() as m:
            m.setattr(indexer, "_filter_parseable", _ordered(how))
            index_repo(root, method="os_walk")
        out[name] = snapshot_edges(root)

    # C: re-save the files of A one by one in reverse sorted order, so a
    # file is re-saved after the files that depend on it (a base class after
    # its subclass, code after the doc that mentions it).
    root = base / "A"
    files = sorted(p for p in root.rglob("*") if p.is_file())
    files = [p for p in files if ".codegraph" not in p.relative_to(root).parts]
    files.reverse()
    if resave_sample is not None:
        files = files[:resave_sample]
    for p in files:
        with p.open("a", encoding="utf-8") as fh:
            fh.write("\n")
        index_file(p, root, force=True)
    out["C"] = snapshot_edges(root)
    reset_connection()
    return out


def _diff(graphs: dict, kind: str, others: tuple[str, ...]) -> list[str]:
    """Readable differences between graph A and each of ``others``."""
    problems: list[str] = []
    left = graphs["A"][kind]
    for label in others:
        right = graphs[label][kind]
        if left == right:
            continue
        problems.append(f"{kind}: graph A vs graph {label}")
        problems += [f"  only in A: {r}" for r in sorted(left - right)[:20]]
        problems += [f"  only in {label}: {r}" for r in sorted(right - left)[:20]]
    return problems


# ---------------------------------------------------------------------------
# Fixture-repo invariance, per backend and per edge kind
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", params=BACKENDS)
def graphs(request, tmp_path_factory):
    if request.param == "duckdb":
        pytest.importorskip("duckdb")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CGH_DB", request.param)
        base = tmp_path_factory.mktemp(f"inv-{request.param}")
        yield build_graphs(FIXTURE, None, base, mp)
    reset_connection()


def test_fixture_exercises_every_edge_kind(graphs):
    seen = {k: frozenset().union(*(g[k] for g in graphs.values())) for k in EDGES}
    empty = [k for k in EXPECTED_NONEMPTY if not seen[k]]
    assert not empty, f"fixture produced no {empty} edges in any graph"


@pytest.mark.parametrize("kind", list(EDGES))
def test_edges_independent_of_ingestion_order(graphs, kind):
    problems = _diff(graphs, kind, ("B-reversed", "B-shuffled"))
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("kind", list(EDGES))
def test_edges_survive_resaving_every_file(graphs, kind):
    problems = _diff(graphs, kind, ("C",))
    assert not problems, "\n".join(problems)


def test_typescript_bases_are_linked(graphs):
    a = graphs["A"]
    assert ("web/src/aa_cart.ts::CartStore", "web/src/zz_store.ts::Store") in a[
        "INHERITS"
    ]
    assert ("web/src/client.ts::TotalWidget", "web/src/widgets.ts::BaseWidget") in a[
        "INHERITS"
    ]
    # this.stash() and super.stash() follow the base, not every stash.
    stash = {t for f, t in a["CALLS"] if f == "web/src/aa_cart.ts::CartStore.persist"}
    assert stash == {"web/src/zz_store.ts::Store.stash"}


def test_cross_file_handler_is_linked(graphs):
    assert ("pkg/urls.py::ANY::/users/<int:pk>/", "pkg/views.py::user_detail") in (
        graphs["A"]["IMPLEMENTED_BY"]
    )


# ---------------------------------------------------------------------------
# The same property on cgh's own source tree
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def self_graphs(tmp_path_factory):
    """cgh's own codegraph/ and docs/ on the SQLite backend (the fixture
    already covers both backends)."""
    src = tmp_path_factory.mktemp("self-src")
    for sub in ("codegraph", "docs"):
        shutil.copytree(
            REPO_ROOT / sub,
            src / sub,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CGH_DB", "sqlite")
        # Plugin scanners add time and nothing to the graph.
        mp.setattr("codegraph.indexer._run_scanners", lambda *a, **k: None)
        base = tmp_path_factory.mktemp("self-inv")
        yield build_graphs(None, src, base, mp)
    reset_connection()


@pytest.mark.parametrize("kind", list(EDGES))
def test_cgh_tree_invariant(self_graphs, kind):
    problems = _diff(self_graphs, kind, ("B-reversed", "B-shuffled", "C"))
    assert not problems, "\n".join(problems)


def test_cgh_tree_has_calls(self_graphs):
    assert len(self_graphs["A"]["CALLS"]) > 1000


def test_terraform_references_cross_files_and_modules(graphs):
    a = graphs["A"]
    assert (
        "infra/main.tf::google_storage_bucket.data",
        "infra/locals.tf::local.bucket_name",
    ) in a["TF_DEPENDS"]
    # module.net.network_id lands on the output of the module's directory,
    # and the module block feeds that directory's variable.
    assert (
        "infra/main.tf::google_compute_instance.vm",
        "infra/modules/net/outputs.tf::output.network_id",
    ) in a["TF_REFS_VAR"]
    assert (
        "infra/main.tf::module.net",
        "infra/modules/net/variables.tf::var.region",
    ) in a["TF_REFS_VAR"]
    # var.region inside the module resolves in the module, never to the root.
    assert (
        "infra/modules/net/main.tf::google_compute_network.n",
        "infra/main.tf::var.region",
    ) not in a["TF_REFS_VAR"]
    assert (
        "infra/terraform.tfvars::tfvars.region",
        "infra/main.tf::var.region",
    ) in a["TF_VAR_REFS"]
    assert (
        "infra/outputs.tf::output.bucket",
        "infra/main.tf::google_storage_bucket.data",
    ) in a["TF_VAR_DEPENDS"]
