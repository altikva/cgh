# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CALLS edges follow the shape of the call and the imports
#              behind it, not every function of the called name. The fixture
#              reproduces the wrong edges a hand check found on cgh's own
#              tree (dict .get, atexit.register, a sqlite commit, an import
#              alias, a third-party module, test doubles) next to the
#              cross-file calls that must stay linked, and the graph it
#              gives must not depend on the order files are indexed in.

from __future__ import annotations

from pathlib import Path

import pytest

from codegraph.analysis import call_rules as cr
from codegraph.analysis.call_rules import Site, Target, is_test_path, targets_for
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_file, index_repo
from codegraph.parsers import get_parser
from codegraph.parsers.base import CallRef

BACKENDS = ["duckdb", "sqlite"]


@pytest.fixture(params=BACKENDS)
def backend(request, monkeypatch):
    if request.param == "duckdb":
        pytest.importorskip("duckdb")
    monkeypatch.setenv("CGH_DB", request.param)
    reset_connection()
    yield request.param
    reset_connection()


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


def _edges(root: Path) -> set[tuple[str, str]]:
    res = get_connection(root).execute("SELECT from_id, to_id FROM edge_calls")
    pre = f"{root}/"
    out = set()
    while res.has_next():
        a, b = res.get_next()
        out.add((a.replace(pre, ""), b.replace(pre, "")))
    return out


def _callees(edges: set[tuple[str, str]], caller: str) -> set[str]:
    return {b for a, b in edges if a == caller}


# ---------------------------------------------------------------------------
# Parsers record the call shape and the import behind it
# ---------------------------------------------------------------------------


def _refs(tmp_path: Path, name: str, body: str) -> dict[str, list[CallRef]]:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    idx = get_parser(path.suffix).parse(path)
    return {fn.name: fn.call_refs for fn in idx.functions}


def test_python_call_shapes(tmp_path):
    refs = _refs(
        tmp_path,
        "m.py",
        "import atexit\n"
        "import a.b as ab\n"
        "from pkg import helper, tool as t\n"
        "from . import views\n"
        "from stars import *\n\n\n"
        "class K(Base):\n"
        "    def go(self, obj):\n"
        "        helper()\n"
        "        t()\n"
        "        self.run()\n"
        "        cls_call = super().go(obj)\n"
        "        atexit.register(self.go)\n"
        "        ab.c.f()\n"
        "        views.show()\n"
        "        obj.x.save()\n"
        "        Manager(obj).get_by_id(1)\n"
        "        mystery()\n"
        "        from late import thing\n"
        "        thing()\n"
        "        return cls_call\n",
    )["go"]
    got = {(r.name, r.receiver, r.module, r.symbol) for r in refs}
    assert ("helper", "", "pkg", "helper") in got
    # An aliased import records the defined name, not the alias.
    assert ("tool", "", "pkg", "tool") in got
    assert ("run", "self", "", "") in got
    assert ("go", "super", "", "") in got
    assert ("register", "atexit", "atexit", "") in got
    assert ("f", "ab.c", "a.b", "") in got
    assert ("show", "views", ".", "views") in got
    assert ("save", "obj.x", "", "") in got
    assert ("get_by_id", "Manager()", "", "") in got
    # Unbound bare names fall back to the star imports.
    assert ("mystery", "", "stars", "*") in got
    # An import inside the function body binds too.
    assert ("thing", "", "late", "thing") in got


def test_python_typed_attribute_calls_name_the_class(tmp_path):
    """self.x.f() on an attribute typed in the class is a call on its class:
    from a class-level annotation, an annotated assignment, a constructor
    call, or an annotated parameter assigned in __init__."""
    refs = _refs(
        tmp_path,
        "h.py",
        "from typing import Optional\n"
        "from app import models as m\n"
        "from app.managers import ReceiptManager, Other\n"
        "from sqlalchemy.ext.asyncio import AsyncSession\n\n\n"
        "class H:\n"
        "    repo: Other\n\n"
        "    def __init__(self, session: AsyncSession, manager: ReceiptManager,\n"
        "                 opt: Optional[m.Cerfa] = None, u: 'Other | None' = None):\n"
        "        self.manager = manager\n"
        "        self.session = session\n"
        "        self.cerfa = opt\n"
        "        self.built = Builder(session)\n"
        "        self.typed: m.Cerfa = make()\n"
        "        self.mixed = Builder(session)\n"
        "        self.loose = make()\n"
        "        self.cleared = None\n\n"
        "    def reset(self):\n"
        "        self.mixed = Other()\n"
        "        self.cleared = None\n\n"
        "    def go(self):\n"
        "        self.manager.get_by_id(1)\n"
        "        self.session.execute(1)\n"
        "        self.cerfa.load()\n"
        "        self.built.run_it()\n"
        "        self.typed.save()\n"
        "        self.repo.find()\n"
        "        self.mixed.go()\n"
        "        self.loose.go()\n"
        "        self.manager.inner.go()\n",
    )["go"]
    got = {(r.name, r.receiver, r.module, r.symbol) for r in refs}
    assert ("get_by_id", "ReceiptManager()", "app.managers", "ReceiptManager") in got
    assert (
        "execute",
        "AsyncSession()",
        "sqlalchemy.ext.asyncio",
        "AsyncSession",
    ) in got
    assert ("load", "m.Cerfa", "app", "models") in got
    assert ("run_it", "Builder()", "", "") in got
    assert ("save", "m.Cerfa", "app", "models") in got
    assert ("find", "Other()", "app.managers", "Other") in got
    # Two classes, or a value of unknown type: the attribute stays untyped.
    assert ("go", "self.mixed", "", "") in got
    assert ("go", "self.loose", "", "") in got
    assert ("go", "self.manager.inner", "", "") in got


def test_typescript_call_shapes(tmp_path):
    refs = _refs(
        tmp_path,
        "m.ts",
        "import api, { load as fetchIt, save } from './api'\n"
        "import * as fmt from '../fmt'\n"
        "import { ref } from 'vue'\n\n"
        "export class W extends B {\n"
        "  draw(): void {\n"
        "    fetchIt(); save(); fmt.pad('x'); this.size(); super.draw();\n"
        "    api.get(); ref(1); new Store().put(); this.repo.find(); autoImported()\n"
        "  }\n"
        "}\n",
    )["draw"]
    got = {(r.name, r.receiver, r.module, r.symbol) for r in refs}
    assert ("load", "", "./api", "load") in got
    assert ("save", "", "./api", "save") in got
    assert ("pad", "fmt", "../fmt", "") in got
    assert ("size", "self", "", "") in got
    assert ("draw", "super", "", "") in got
    assert ("get", "api", "./api", "default") in got
    assert ("ref", "", "vue", "ref") in got
    assert ("put", "Store()", "", "") in got
    assert ("find", "this.repo", "", "") in got
    assert ("autoImported", "", "", "") in got


def test_typescript_typed_fields_name_the_class(tmp_path):
    refs = _refs(
        tmp_path,
        "w.ts",
        "import { Repo } from './repo'\n"
        "import * as ns from './ns'\n\n"
        "export class W {\n"
        "  private store: Store;\n"
        "  cache = new Cache();\n"
        "  opt?: Repo | null;\n"
        "  deep: ns.Deep;\n"
        "  mixed: Repo | Store;\n"
        "  constructor(private readonly repo: Repo, public x: string, plain: P) {}\n"
        "  go(): void {\n"
        "    this.store.save(); this.cache.put(); this.opt.find(); this.repo.load()\n"
        "    this.x.trim(); this.plain.z(); this.deep.q(); this.mixed.m()\n"
        "  }\n"
        "}\n",
    )["go"]
    got = {(r.name, r.receiver, r.module, r.symbol) for r in refs}
    assert ("save", "Store()", "", "") in got
    assert ("put", "Cache()", "", "") in got
    assert ("find", "Repo()", "./repo", "Repo") in got
    assert ("load", "Repo()", "./repo", "Repo") in got
    assert ("q", "ns.Deep", "./ns", "") in got
    # A builtin type, a plain parameter, a union of classes: untyped.
    assert ("trim", "this.x", "", "") in got
    assert ("z", "this.plain", "", "") in got
    assert ("m", "this.mixed", "", "") in got


# ---------------------------------------------------------------------------
# The rules, on candidate lists
# ---------------------------------------------------------------------------


def _t(path: str, cls: str = "", bases: tuple[str, ...] = (), name: str = "f"):
    qual = f"{cls}.{name}" if cls else name
    return Target(f"{path}::{qual}", name, path, cls, bases)


def _ids(targets: list[Target]) -> set[str]:
    return {t.id for t in targets}


ROOT = "/r"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/r/tests/test_a.py", True),
        ("/r/pkg/conftest.py", True),
        ("/r/pkg/test_util.py", True),
        ("/r/pkg/util_test.go", True),
        ("/r/web/a.spec.ts", True),
        ("/r/web/a.test.tsx", True),
        ("/r/web/__tests__/a.ts", True),
        ("/r/pkg/testing_tools.py", False),
        ("/r/pkg/contest.py", False),
        # The repo itself living under a directory named tests/ is not a test.
        ("/home/tests/r/pkg/a.py", False),
    ],
)
def test_is_test_path(path, expected):
    root = "/home/tests/r" if path.startswith("/home") else ROOT
    assert is_test_path(path, root) is expected


def test_bare_call_prefers_same_file_then_the_imported_file():
    cands = [_t("/r/a.py"), _t("/r/b.py"), _t("/r/c.py")]
    local = Site("/r/a.py::g", "/r/a.py", "f", cr.BARE, "/r/c.py")
    assert _ids(targets_for(local, cands, ROOT)) == {"/r/a.py::f"}
    imported = Site("/r/x.py::g", "/r/x.py", "f", cr.BARE, "/r/c.py")
    assert _ids(targets_for(imported, cands, ROOT)) == {"/r/c.py::f"}
    # Imported from a package: what the package's modules define.
    pkg = [_t("/r/pkg/impl.py"), _t("/r/other.py")]
    site = Site("/r/x.py::g", "/r/x.py", "f", cr.BARE, "/r/pkg/__init__.py")
    assert _ids(targets_for(site, pkg, ROOT)) == {"/r/pkg/impl.py::f"}
    # Re-exported from a file that defines nothing of that name: by name.
    site = Site("/r/x.py::g", "/r/x.py", "f", cr.BARE, "/r/reexport.py")
    assert _ids(targets_for(site, cands, ROOT)) == _ids(cands)


def test_bare_call_never_reaches_a_method_or_an_external_import():
    cands = [_t("/r/a.py", cls="K"), _t("/r/b.py")]
    site = Site("/r/x.py::g", "/r/x.py", "f", cr.BARE, "")
    assert _ids(targets_for(site, cands, ROOT)) == {"/r/b.py::f"}
    site = Site("/r/x.py::g", "/r/x.py", "f", cr.BARE, cr.EXTERNAL)
    assert targets_for(site, cands, ROOT) == []


def test_self_call_prefers_own_class_then_bases():
    own = _t("/r/a.py", cls="Child")
    base = _t("/r/base.py", cls="Base")
    other = _t("/r/z.py", cls="Unrelated")
    site = Site("/r/a.py::Child.g", "/r/a.py", "f", cr.SELF, "", "Child:Base")
    assert _ids(targets_for(site, [own, base, other], ROOT)) == {own.id}
    assert _ids(targets_for(site, [base, other], ROOT)) == {base.id}
    # super().f() skips the own class.
    sup = Site("/r/a.py::Child.f", "/r/a.py", "f", cr.SELF, "", ":Base")
    assert _ids(targets_for(sup, [own, base, other], ROOT)) == {base.id}


def test_self_call_on_a_library_base_does_not_guess_from_the_file():
    """super().__init__() of an Exception subclass is Exception's, never the
    __init__ of the other classes the same file defines."""
    sibling = _t("/r/exc.py", cls="Other", name="__init__")
    many = [_t(f"/r/m{i}.py", cls=f"C{i}", name="__init__") for i in range(5)]
    site = Site(
        "/r/exc.py::E.__init__", "/r/exc.py", "__init__", cr.SELF, "", ":Exception"
    )
    assert targets_for(site, [sibling, *many], ROOT) == []


def test_module_attribute_call():
    cands = [_t("/r/lib.py"), _t("/r/other.py"), _t("/r/k.py", cls="K")]
    site = Site("/r/x.py::g", "/r/x.py", "f", cr.MOD, "/r/lib.py")
    assert _ids(targets_for(site, cands, ROOT)) == {"/r/lib.py::f"}
    ext = Site("/r/x.py::g", "/r/x.py", "f", cr.MOD, cr.EXTERNAL)
    assert targets_for(ext, cands, ROOT) == []


def test_unknown_receiver_links_methods_only_and_few():
    fn = _t("/r/cache.py", name="lookup")
    few = [_t(f"/r/s{i}.py", cls=f"S{i}", name="lookup") for i in range(3)]
    site = Site("/r/x.py::g", "/r/x.py", "lookup", cr.ATTR)
    assert _ids(targets_for(site, [fn, *few], ROOT)) == _ids(few)
    many = [*few, _t("/r/s9.py", cls="S9", name="lookup")]
    assert targets_for(site, [fn, *many], ROOT) == []
    # A file the caller imports names the class.
    hit = targets_for(site, many, ROOT, frozenset({"/r/s9.py"}))
    assert _ids(hit) == {"/r/s9.py::S9.lookup"}


def test_unknown_receiver_skips_the_callers_own_class():
    """self.manager.update() inside Handler.update calls the manager."""
    own = _t("/r/h.py", cls="Handler", name="update")
    mgr = _t("/r/m.py", cls="Manager", name="update")
    site = Site("/r/h.py::Handler.update", "/r/h.py", "update", cr.ATTR)
    assert _ids(targets_for(site, [own, mgr], ROOT, frozenset({"/r/m.py"}))) == {mgr.id}


def test_common_method_on_an_attribute_skips_the_callers_bases():
    """self.session.flush() in a BaseManager subclass is the session's."""
    base = _t("/r/base.py", cls="BaseManager", name="flush")
    site = Site("/r/m.py::M.save", "/r/m.py", "flush", cr.ATTR, "", "M:BaseManager")
    assert targets_for(site, [base], ROOT, frozenset({"/r/base.py"})) == []


def test_unknown_receiver_goes_through_the_overridden_method():
    base = _t("/r/base.py", cls="BaseManager", name="get_by_id")
    subs = [
        _t(f"/r/m{i}.py", cls=f"M{i}", bases=("BaseManager",), name="get_by_id")
        for i in range(5)
    ]
    site = Site("/r/h.py::H.show", "/r/h.py", "get_by_id", cr.ATTR)
    assert _ids(targets_for(site, [base, *subs], ROOT)) == {base.id}


def test_common_library_method_names_need_an_import():
    """session.add / data.get / conn.commit name nothing in the repo."""
    only = _t("/r/files.py", cls="FileManager", name="add")
    site = Site("/r/x.py::g", "/r/x.py", "add", cr.ATTR)
    assert targets_for(site, [only], ROOT) == []
    assert _ids(targets_for(site, [only], ROOT, frozenset({"/r/files.py"}))) == {
        only.id
    }


def test_class_receiver_links_that_class():
    mine = _t("/r/order.py", cls="OrderManager", name="get_by_id")
    other = _t("/r/user.py", cls="UserManager", name="get_by_id")
    site = Site(
        "/r/x.py::g", "/r/x.py", "get_by_id", cr.CLS, "/r/order.py", "OrderManager"
    )
    assert _ids(targets_for(site, [mine, other], ROOT)) == {mine.id}


def test_class_receiver_climbs_to_the_nearest_base_defining_the_method():
    """self.manager.get_by_id() with manager a ReceiptManager(BaseManager) is
    BaseManager's, even when the caller imports another file defining a
    get_by_id: the receiver's class beats the imported-file guess."""
    base = _t("/r/base.py", cls="BaseManager", name="get_by_id")
    mid = _t("/r/mid.py", cls="MidManager", bases=("BaseManager",), name="get_by_id")
    cerfa = _t("/r/cerfa.py", cls="CerfaManager", name="get_by_id")
    site = Site(
        "/r/h.py::H.show",
        "/r/h.py",
        "get_by_id",
        cr.CLS,
        "/r/receipt.py",
        "ReceiptManager",
    )
    imported = frozenset({"/r/cerfa.py"})
    hierarchy = {"ReceiptManager": ("MidManager",), "MidManager": ("BaseManager",)}
    hit = targets_for(site, [base, mid, cerfa], ROOT, imported, hierarchy)
    assert _ids(hit) == {mid.id}
    hit = targets_for(site, [base, cerfa], ROOT, imported, hierarchy)
    assert _ids(hit) == {base.id}
    # A cycle in the recorded bases ends the climb.
    loop = {"ReceiptManager": ("A",), "A": ("ReceiptManager",)}
    assert _ids(targets_for(site, [base, cerfa], ROOT, imported, loop)) == {cerfa.id}
    # Without the hierarchy, the old guess from the imported file.
    assert _ids(targets_for(site, [base, cerfa], ROOT, imported)) == {cerfa.id}


def test_production_code_never_reaches_tests_and_languages_never_mix():
    double = _t("/r/tests/fakes.py", cls="Fake", name="save_all")
    ts = _t("/r/web/a.ts", name="save_all")
    prod = Site("/r/app.py::g", "/r/app.py", "save_all", cr.ATTR)
    assert targets_for(prod, [double, ts], ROOT) == []
    test = Site("/r/tests/test_a.py::t", "/r/tests/test_a.py", "save_all", cr.ATTR)
    assert _ids(targets_for(test, [double, ts], ROOT)) == {double.id}
    legacy = Site("/r/app.go::g", "/r/app.go", "save_all", "")
    assert targets_for(legacy, [double, ts], ROOT) == []


# ---------------------------------------------------------------------------
# On an indexed tree: the wrong edges are gone, the right ones stay
# ---------------------------------------------------------------------------

FIXTURE: dict[str, str] = {
    # Look-alikes the wrong edges used to land on.
    "pkg/__init__.py": "def _get_conn():\n    return 1\n",
    "pkg/call_log.py": "def _get_conn():\n    return 2\n",
    "pkg/cache.py": "def get(key):\n    return key\n",
    "pkg/fts.py": "def commit(conn):\n    return conn\n",
    "pkg/plugins.py": "def register(api):\n    return api\n",
    "pkg/rust.py": "def _ident(node):\n    return node\n",
    "pkg/nuxt.py": "class NuxtConfigParser:\n    def parse(self, path):\n        return path\n",
    "pkg/utils.py": "def normalize_identifier(name):\n    return name\n",
    "pkg/history.py": (
        "from pkg import _get_conn\n\n\ndef history():\n    return _get_conn()\n"
    ),
    "pkg/store.py": (
        "import atexit\n\n"
        "import parso\n\n"
        "from pkg.utils import normalize_identifier as _ident\n\n\n"
        "class Store:\n"
        "    def __init__(self, conn):\n"
        "        self._conn = conn\n\n"
        "    def save(self, data):\n"
        "        atexit.register(self.close)\n"
        "        value = data.get('k')\n"
        "        self._conn.commit()\n"
        "        tree = parso.parse('x')\n"
        "        return _ident(value), tree\n\n"
        "    def close(self):\n"
        "        return None\n"
    ),
    "pkg/service.py": "def flush_all(store):\n    return store.save_all()\n",
    "tests/__init__.py": "",
    "tests/fakes.py": "class FakeStore:\n    def save_all(self):\n        return 1\n",
    "tests/test_store.py": (
        "from tests.fakes import FakeStore\n\n\n"
        "def test_flush():\n    return FakeStore().save_all()\n"
    ),
    # Cross-file calls that must stay linked.
    "lib.py": "def helper():\n    return 1\n",
    "app.py": (
        "import lib as L\n"
        "from lib import helper\n\n\n"
        "def run():\n    return helper()\n\n\n"
        "def run_alias():\n    return L.helper()\n\n\n"
        "def run_late():\n    from lib import helper as h\n\n    return h()\n"
    ),
    "managers/__init__.py": "",
    "managers/base.py": (
        "class BaseManager:\n"
        "    def __init__(self, session):\n"
        "        self.session = session\n\n"
        "    def get_by_id(self, i):\n"
        "        return i\n"
    ),
    **{
        f"managers/m{i}.py": (
            "from managers.base import BaseManager\n\n\n"
            f"class M{i}Manager(BaseManager):\n"
            "    def get_by_id(self, i):\n"
            "        return super().get_by_id(i)\n"
        )
        for i in range(4)
    },
    "managers/plain.py": (
        "from managers.base import BaseManager\n\n\n"
        "class PlainManager(BaseManager):\n"
        "    def __init__(self, session):\n"
        "        super().__init__(session)\n"
    ),
    "handlers/__init__.py": "",
    "handlers/user.py": (
        "from managers.plain import PlainManager\n\n\n"
        "class UserHandler:\n"
        "    def __init__(self, session):\n"
        "        self.manager = PlainManager(session)\n\n"
        "    def show(self, i):\n"
        "        return self.manager.get_by_id(i)\n"
    ),
    "handlers/direct.py": (
        "from managers.m1 import M1Manager\n\n\n"
        "def direct(s, i):\n"
        "    return M1Manager(s).get_by_id(i)\n"
    ),
    # A typed attribute: its class's inherited method, not the one of a file
    # the handler also imports.
    "managers/receipt.py": (
        "from managers.base import BaseManager\n\n\n"
        "class ReceiptManager(BaseManager):\n"
        "    def list_for_org(self):\n"
        "        return 1\n"
    ),
    "managers/mid.py": (
        "from managers.base import BaseManager\n\n\nclass MidManager(BaseManager):\n    pass\n"
    ),
    "managers/deep.py": (
        "from managers.mid import MidManager\n\n\nclass DeepManager(MidManager):\n    pass\n"
    ),
    "managers/cerfa.py": (
        "class CerfaManager:\n    def get_by_id(self, i):\n        return i\n"
    ),
    "handlers/receipt.py": (
        "from managers.cerfa import CerfaManager\n"
        "from managers.deep import DeepManager\n"
        "from managers.receipt import ReceiptManager\n\n\n"
        "class ReceiptHandler:\n"
        "    deep: DeepManager\n\n"
        "    def __init__(self, session, manager: ReceiptManager):\n"
        "        self.manager = manager\n"
        "        self.cerfa = CerfaManager(session)\n\n"
        "    def show(self, i):\n"
        "        return self.manager.get_by_id(i)\n\n"
        "    def cerfa_of(self, i):\n"
        "        return self.cerfa.get_by_id(i)\n\n"
        "    def deep_of(self, i):\n"
        "        return self.deep.get_by_id(i)\n"
    ),
    "web/repo.ts": (
        "export class BaseRepo {\n  find(): number {\n    return 1\n  }\n}\n"
        "export class UserRepo extends BaseRepo {}\n"
    ),
    "web/other.ts": "export class Other {\n  find(): number {\n    return 2\n  }\n}\n",
    "web/service.ts": (
        "import { UserRepo } from './repo'\n"
        "import { Other } from './other'\n\n"
        "export class UserService {\n"
        "  constructor(private repo: UserRepo, private other: Other) {}\n"
        "  one(): number {\n    return this.repo.find()\n  }\n"
        "}\n"
    ),
    "web/format.ts": "export function pad(s: string): string {\n  return ' ' + s\n}\n",
    "web/client.ts": (
        "import * as fmt from './format'\n"
        "import { pad as padLeft } from './format'\n\n"
        "export function total(n: number): string {\n"
        "  return padLeft(String(n)) + fmt.pad('!')\n"
        "}\n"
    ),
}


@pytest.fixture
def indexed(tmp_path, backend):
    _write(tmp_path, FIXTURE)
    index_repo(tmp_path, method="os_walk")
    return _edges(tmp_path)


def test_wrong_edges_are_gone(indexed):
    save = _callees(indexed, "pkg/store.py::Store.save")
    for wrong in (
        "pkg/cache.py::get",  # dict .get
        "pkg/plugins.py::register",  # atexit.register
        "pkg/fts.py::commit",  # self._conn.commit() on a sqlite connection
        "pkg/rust.py::_ident",  # an import alias of another function
        "pkg/nuxt.py::NuxtConfigParser.parse",  # parso.parse
    ):
        assert wrong not in save
    assert _callees(indexed, "pkg/history.py::history") == {
        "pkg/__init__.py::_get_conn"
    }


def test_aliased_import_links_the_aliased_function(indexed):
    assert "pkg/utils.py::normalize_identifier" in _callees(
        indexed, "pkg/store.py::Store.save"
    )


def test_no_production_edge_into_test_files(indexed):
    assert _callees(indexed, "pkg/service.py::flush_all") == set()
    assert _callees(indexed, "tests/test_store.py::test_flush") == {
        "tests/fakes.py::FakeStore.save_all"
    }
    assert not [
        (a, b)
        for a, b in indexed
        if not a.startswith("tests/") and b.startswith("tests/")
    ]


def test_cross_file_calls_stay(indexed):
    assert _callees(indexed, "app.py::run") == {"lib.py::helper"}
    assert _callees(indexed, "app.py::run_alias") == {"lib.py::helper"}
    assert _callees(indexed, "app.py::run_late") == {"lib.py::helper"}
    # self.manager.get_by_id(): the method every manager overrides from.
    assert _callees(indexed, "handlers/user.py::UserHandler.show") == {
        "managers/base.py::BaseManager.get_by_id"
    }
    # M1Manager(s).get_by_id(): that class's own method.
    assert _callees(indexed, "handlers/direct.py::direct") == {
        "managers/m1.py::M1Manager.get_by_id"
    }
    assert _callees(indexed, "managers/m2.py::M2Manager.get_by_id") == {
        "managers/base.py::BaseManager.get_by_id"
    }
    assert _callees(indexed, "managers/plain.py::PlainManager.__init__") == {
        "managers/base.py::BaseManager.__init__"
    }
    assert _callees(indexed, "web/client.ts::total") == {"web/format.ts::pad"}


def test_typed_attribute_beats_the_imported_file(indexed):
    base = "managers/base.py::BaseManager.get_by_id"
    assert _callees(indexed, "handlers/receipt.py::ReceiptHandler.show") == {base}
    assert _callees(indexed, "handlers/receipt.py::ReceiptHandler.deep_of") == {base}
    assert _callees(indexed, "handlers/receipt.py::ReceiptHandler.cerfa_of") == {
        "managers/cerfa.py::CerfaManager.get_by_id"
    }
    assert _callees(indexed, "web/service.ts::UserService.one") == {
        "web/repo.ts::BaseRepo.find"
    }


def test_a_base_class_change_relinks_the_typed_calls(tmp_path, backend):
    """The method a typed call reaches depends on the bases of classes in
    other files: a change to any of them, in whichever order files come,
    moves the edge."""
    files = {
        "h.py": (
            "from deep import Deep\n\n\n"
            "class H:\n"
            "    def __init__(self, d: Deep):\n"
            "        self.d = d\n\n"
            "    def go(self):\n"
            "        return self.d.load()\n"
        ),
        "deep.py": "from mid import Mid\n\n\nclass Deep(Mid):\n    pass\n",
        "mid.py": "from base import Base\n\n\nclass Mid(Base):\n    pass\n",
        "base.py": "class Base:\n    def load(self):\n        return 1\n",
        "other.py": "class Other:\n    def load(self):\n        return 2\n",
    }
    _write(tmp_path, files)
    for rel in ("h.py", "base.py", "other.py", "deep.py", "mid.py"):
        assert index_file(tmp_path / rel, tmp_path)
    assert _callees(_edges(tmp_path), "h.py::H.go") == {"base.py::Base.load"}

    (tmp_path / "mid.py").write_text(
        "from other import Other\n\n\nclass Mid(Other):\n    pass\n", encoding="utf-8"
    )
    assert index_file(tmp_path / "mid.py", tmp_path, force=True)
    assert _callees(_edges(tmp_path), "h.py::H.go") == {"other.py::Other.load"}

    (tmp_path / "mid.py").unlink()
    from codegraph.indexer import _delete_file_relinking

    _delete_file_relinking(get_connection(tmp_path), str(tmp_path / "mid.py"), tmp_path)
    # Deep's base is gone: the class is known but defines no load, and the
    # two methods of the name are few, so both stay candidates.
    assert _callees(_edges(tmp_path), "h.py::H.go") == {
        "base.py::Base.load",
        "other.py::Other.load",
    }


def test_fixture_is_order_invariant(tmp_path, backend, monkeypatch):
    from tests.test_invariants.test_graph_invariance import build_graphs

    graphs = build_graphs(FIXTURE, None, tmp_path, monkeypatch)
    calls = {label: g["CALLS"] for label, g in graphs.items()}
    assert calls["A"], "fixture produced no CALLS edges"
    for label in ("B-reversed", "B-shuffled", "C"):
        assert calls[label] == calls["A"], label


def test_a_definition_appearing_or_leaving_updates_other_callers(tmp_path, backend):
    """The few-methods rule depends on every method of the name: a fourth
    one appearing in another file takes the edges away, its deletion brings
    them back, in whichever order files come."""
    files = {
        "a.py": "class A:\n    def lookup(self):\n        return 1\n",
        "b.py": "class B:\n    def lookup(self):\n        return 2\n",
        "user.py": "def use(obj):\n    return obj.lookup()\n",
    }
    _write(tmp_path, files)
    for rel in ("user.py", "a.py", "b.py"):
        assert index_file(tmp_path / rel, tmp_path)
    assert _callees(_edges(tmp_path), "user.py::use") == {
        "a.py::A.lookup",
        "b.py::B.lookup",
    }

    _write(
        tmp_path,
        {
            f"c{i}.py": f"class C{i}:\n    def lookup(self):\n        return 3\n"
            for i in range(2)
        },
    )
    for i in range(2):
        assert index_file(tmp_path / f"c{i}.py", tmp_path)
    assert _callees(_edges(tmp_path), "user.py::use") == set()

    (tmp_path / "c1.py").write_text("X = 1\n", encoding="utf-8")
    assert index_file(tmp_path / "c1.py", tmp_path, force=True)
    assert _callees(_edges(tmp_path), "user.py::use") == {
        "a.py::A.lookup",
        "b.py::B.lookup",
        "c0.py::C0.lookup",
    }


def test_old_call_site_table_gains_the_shape_columns(tmp_path, backend):
    """An index written before call shapes existed opens and takes the new
    rows (its full reparse comes from the GRAPH_FORMAT bump)."""
    db_dir = tmp_path / ".codegraph"
    db_dir.mkdir()
    if backend == "sqlite":
        import sqlite3

        con = sqlite3.connect(db_dir / "graph.sqlite")
        con.execute(
            "CREATE TABLE call_site (from_id TEXT, file_path TEXT, name TEXT, "
            "to_id TEXT NOT NULL DEFAULT '')"
        )
        con.commit()
        con.close()
    else:
        import duckdb

        con = duckdb.connect(str(db_dir / "graph.duckdb"))
        con.execute(
            "CREATE TABLE call_site (from_id TEXT, file_path TEXT, name TEXT, "
            "to_id TEXT NOT NULL DEFAULT '')"
        )
        con.close()
    conn = get_connection(tmp_path)
    conn.replace_call_sites("a.py", [("a.py::f", "g", "", "bare", "b.py", "")])
    rows = conn.call_sites_into(["g"], [], "other.py")
    assert rows == [("a.py::f", "a.py", "g", "", "bare", "b.py", "")]
