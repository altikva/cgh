# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The Terraform graph end to end, on both backends: blocks
#              stored with their addresses, reference edges scoped to the
#              module directory, local module linking (inputs and outputs,
#              never a remote source), and the query tools answering from
#              those edges (symbol_lookup, find_callers, find_callees,
#              impact_of, cgh impact). Also the text side: .tfvars entries
#              and contracts/ YAML entries in the FTS.

from __future__ import annotations

import json
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.analysis.callers import callers_of
from codegraph.analysis.impact import build_impact_report
from codegraph.core.db import get_connection, reset_connection
from codegraph.core.fts import fts_search, get_fts_conn
from codegraph.indexer import index_file, index_repo

FILES = {
    "env/variables.tf": (
        'variable "region" {\n  type = string\n}\n\nvariable "project_id" {}\n'
    ),
    "env/main.tf": (
        'resource "google_storage_bucket" "data" {\n'
        "  location = var.region\n"
        "  project  = var.project_id\n"
        "}\n\n"
        'module "iam" {\n'
        '  source     = "../modules/iam"\n'
        "  project_id = var.project_id\n"
        "  region     = var.region\n"
        "}\n\n"
        'module "net" {\n'
        '  source  = "git::https://example.com/net.git?ref=v1"\n'
        "  region  = var.region\n"
        "}\n\n"
        'resource "google_storage_bucket_iam_member" "reader" {\n'
        "  bucket = google_storage_bucket.data.name\n"
        "  member = module.iam.sa_email\n"
        "}\n"
    ),
    "env/outputs.tf": (
        'output "bucket" {\n  value = google_storage_bucket.data.url\n}\n'
    ),
    "env/terraform.tfvars": 'region = "europe-west1"\nproject_id = "acme-prod"\n',
    "modules/iam/variables.tf": ('variable "project_id" {}\n\nvariable "region" {}\n'),
    "modules/iam/main.tf": (
        'resource "google_service_account" "sa" {\n  project = var.project_id\n}\n'
    ),
    "modules/iam/outputs.tf": (
        'output "sa_email" {\n  value = google_service_account.sa.email\n}\n'
    ),
    "contracts/env-vars.yaml": (
        "services:\n"
        "  api:\n"
        "    DATABASE_URL:\n"
        "      source: terraform_output\n"
        "      tf_output: module.cloudsql.connection_string\n"
        "    REDIS_URL:\n"
        "      source: terraform_output\n"
        "  worker:\n"
        "    DATABASE_URL:\n"
        "      source: secret_manager\n"
    ),
}


@pytest.fixture(params=["duckdb", "sqlite"])
def repo(request, tmp_path, monkeypatch):
    if request.param == "duckdb":
        pytest.importorskip("duckdb")
    monkeypatch.setenv("CGH_DB", request.param)
    reset_connection()
    root = tmp_path.resolve()
    for rel, body in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    index_repo(root, method="os_walk")
    _srv._root = root
    _srv._conn = None
    yield root
    reset_connection()
    _srv._root = None
    _srv._conn = None


def _rel(root: Path, value: str) -> str:
    return value.replace(f"{root}/", "")


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _tools() -> dict:
    from codegraph.server.tools_insight import register as register_insight
    from codegraph.server.tools_query import register as register_query

    m = _FakeMcp()
    register_query(m)
    register_insight(m)
    return m.tools


def _callers(root: Path, name: str) -> set[tuple[str, str]]:
    conn = get_connection(root)
    return {(c["caller"], _rel(root, c["file"])) for c in callers_of(conn, name)}


def test_blocks_are_stored_with_address_and_directory(repo):
    conn = get_connection(repo)
    rows = conn.find_nodes(
        "TFResource",
        where={"address": "module.iam"},
        return_fields=["kind", "module_dir", "source_dir", "type"],
    )
    assert rows == [
        {
            "kind": "module",
            "module_dir": str(repo / "env"),
            "source_dir": str(repo / "modules/iam"),
            "type": "../modules/iam",
        }
    ]
    remote = conn.find_nodes(
        "TFResource", where={"address": "module.net"}, return_fields=["source_dir"]
    )
    assert remote == [{"source_dir": ""}]
    tfvars = conn.find_nodes(
        "TFVar", where={"kind": "tfvars"}, return_fields=["address"]
    )
    assert {r["address"] for r in tfvars} == {"region", "project_id"}


def test_references_resolve_within_the_module_directory(repo):
    # The root's var.region is used by the root blocks and set by tfvars,
    # never by the module's blocks, which have their own var.region.
    assert _callers(repo, "var.region") >= {
        ("google_storage_bucket.data", "env/main.tf"),
        ("module.iam", "env/main.tf"),
        ("module.net", "env/main.tf"),
        ("region", "env/terraform.tfvars"),
    }
    module_users = _callers(repo, "var.project_id")
    assert ("google_service_account.sa", "modules/iam/main.tf") in module_users
    assert ("google_storage_bucket.data", "env/main.tf") in module_users


def test_resource_callers(repo):
    assert _callers(repo, "google_storage_bucket.data") == {
        ("google_storage_bucket_iam_member.reader", "env/main.tf"),
        ("output.bucket", "env/outputs.tf"),
    }


def test_local_module_inputs_and_outputs_are_linked(repo):
    conn = get_connection(repo)
    inputs = {
        _rel(repo, r["dst_file_path"])
        for r in conn.find_neighbors(
            "TF_REFS_VAR",
            src_where={"address": "module.iam"},
            return_dst=["file_path"],
        )
    }
    assert "modules/iam/variables.tf" in inputs
    # module.iam.sa_email lands on the module's output block.
    assert ("google_storage_bucket_iam_member.reader", "env/main.tf") in _callers(
        repo, "output.sa_email"
    )
    # A remote source links nothing: module.net reaches only the root vars.
    targets = {
        _rel(repo, r["dst_file_path"])
        for r in conn.find_neighbors(
            "TF_REFS_VAR", src_where={"address": "module.net"}, return_dst=["file_path"]
        )
    }
    assert targets == {"env/variables.tf"}


def test_module_output_link_survives_reindexing_either_side(repo):
    want = ("google_storage_bucket_iam_member.reader", "env/main.tf")
    for rel in ("modules/iam/outputs.tf", "env/main.tf", "modules/iam/outputs.tf"):
        p = repo / rel
        p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        index_file(p, repo, force=True)
        assert want in _callers(repo, "output.sa_email"), rel


def test_symbol_lookup_by_address_and_by_name(repo):
    tools = _tools()
    out = json.loads(tools["symbol_lookup"]("var.region"))
    files = sorted(_rel(repo, d["file"]) for d in out["definitions"])
    assert files == ["env/variables.tf", "modules/iam/variables.tf"]
    assert all(d["kind"] == "tf_var" and d["lines"] for d in out["definitions"])
    mod = json.loads(tools["symbol_lookup"]("module.iam"))["definitions"]
    assert [(d["kind"], d["lines"]) for d in mod] == [("tf_module", "6-10")]
    # The bare block name still finds the resource, as before addresses.
    by_name = json.loads(tools["symbol_lookup"]("data"))["definitions"]
    assert {d["name"] for d in by_name} == {"google_storage_bucket.data"}


def test_search_symbols_kinds_filter(repo):
    tools = _tools()
    out = json.loads(tools["search_symbols"]("iam", kinds="tf_module"))
    assert {r["name"] for r in out["results"]} == {"module.iam"}
    out = json.loads(tools["search_symbols"]("region", kinds="tf_var"))
    assert {r["type"] for r in out["results"]} == {"variable", "tfvars"}


def test_find_callers_and_callees_tools(repo):
    tools = _tools()
    callers = json.loads(tools["find_callers"]("google_service_account.sa"))
    assert [c["caller"] for c in callers["callers"]] == ["output.sa_email"]
    callees = json.loads(tools["find_callees"]("google_storage_bucket.data"))
    assert {c["callee"] for c in callees["callees"]} == {"var.region", "var.project_id"}


def test_impact_of_a_tf_file_and_an_address(repo):
    tools = _tools()
    target = str(repo / "modules/iam/outputs.tf")
    out = json.loads(tools["impact_of"](target, max_depth=1))
    assert out["direction"] == "importers"
    assert {_rel(repo, r["node"]) for r in out["impacted"]} == {"env/main.tf"}
    # Further out: env/main.tf's bucket is used by env/outputs.tf.
    out = json.loads(tools["impact_of"](target, max_depth=2))
    assert {_rel(repo, r["node"]) for r in out["impacted"]} == {
        "env/main.tf",
        "env/outputs.tf",
    }
    out = json.loads(tools["impact_of"]("var.project_id", max_depth=2))
    nodes = {_rel(repo, r["node"]) for r in out["impacted"]}
    assert "env/main.tf::google_storage_bucket.data" in nodes
    # Two hops: the bucket's own dependents.
    assert "env/outputs.tf::output.bucket" in nodes
    assert {r["node_kind"] for r in out["impacted"]} == {"tf_block"}


def test_cgh_impact_report_on_a_tf_change(repo):
    conn = get_connection(repo)
    report = build_impact_report(conn, str(repo), ["modules/iam/variables.tf"])
    assert {s["name"] for s in report["changed_symbols"]} == {
        "var.project_id",
        "var.region",
    }
    impacted = {r["file"] for r in report["impacted"]}
    # The module block feeding the inputs, then whatever uses its outputs.
    assert "env/main.tf" in impacted
    assert "modules/iam/main.tf" in impacted


def test_text_search_reaches_tfvars_and_contracts(repo):
    conn = get_fts_conn(repo)
    hits = fts_search(conn, "acme-prod", limit=5)
    assert any(h.kind == "tf_tfvars" and h.name == "project_id" for h in hits)
    hits = fts_search(conn, "connection_string", limit=5)
    assert any(h.name == "services.api.DATABASE_URL" for h in hits)
    by_kind = fts_search(conn, "region", limit=20, kind_filter="tf_variable")
    assert {h.name for h in by_kind} == {"var.region"}


def test_contract_entries_get_their_own_line(repo):
    conn = get_connection(repo)
    rows = conn.find_nodes(
        "MdSection",
        contains={"title": "DATABASE_URL"},
        return_fields=["title", "start_line"],
    )
    assert {(r["title"], r["start_line"]) for r in rows} == {
        ("services.api.DATABASE_URL", 3),
        ("services.worker.DATABASE_URL", 9),
    }
