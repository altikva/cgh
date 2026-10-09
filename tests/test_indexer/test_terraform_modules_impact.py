# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Terraform module inputs as searchable entries, moved blocks
#              linked to the addresses they name, remote module sources
#              mapped to a local checkout by [terraform] module_sources (a
#              separate "modules" repo read on demand, never written), and
#              the block-precise impact of a .tf change, on both backends.

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.analysis.callers import callers_of
from codegraph.analysis.impact import (
    build_impact_report,
    format_changed_entry,
    split_changed_entry,
)
from codegraph.analysis.terraform import ModuleSources
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_file, index_repo

MAIN = (
    'module "kms" {\n'
    '  source       = "git::https://github.com/acme/tf-modules.git//modules/kms?ref=v0.1.0"\n'
    '  keyring_name = "acme-data-keys"\n'
    "  members      = [var.sa]\n"
    "}\n\n"
    'module "reg" {\n'
    '  source       = "acme/kms/google"\n'
    '  version      = "1.0.0"\n'
    '  keyring_name = "acme-reg-keys"\n'
    "}\n\n"
    'module "net" {\n'
    '  source = "git::https://github.com/acme/inrepo//net"\n'
    '  cidr   = "10.0.0.0/8"\n'
    "}\n\n"
    'resource "google_x" "user" {\n'
    "  key = module.kms.keyring_id\n"
    "}\n\n"
    'resource "google_y" "base" {\n'
    '  name = "base"\n'
    "}\n"
)

REPO = {
    "env/main.tf": MAIN,
    "env/variables.tf": 'variable "sa" {}\n',
    "env/other.tf": (
        'resource "google_y" "unrelated" {\n  name = google_y.base.name\n}\n'
    ),
    "env/third.tf": 'resource "google_z" "after" {\n  x = google_x.user.id\n}\n',
    "env/moved.tf": (
        "moved {\n"
        "  from = module.kms.google_kms_key_ring.old\n"
        "  to   = module.kms.google_kms_key_ring.this\n"
        "}\n"
    ),
    # A module mapped inside the repo itself: indexed like any directory.
    "shared/net/variables.tf": 'variable "cidr" {}\n',
    "contracts/env-vars.yaml": (
        "services:\n"
        "  api:\n"
        "    KMS_KEYRING:\n"
        "      source: terraform_output\n"
        "      tf_output: module.kms.keyring_id\n"
    ),
    ".codegraph/config.toml": (
        "[terraform]\n"
        "module_sources = {"
        ' "git::https://github.com/acme/tf-modules" = "../tf-modules",'
        ' "acme/kms/google" = "../tf-modules/modules/kms",'
        ' "https://github.com/acme/inrepo" = "shared" }\n'
    ),
}

MODULES = {
    "modules/kms/variables.tf": (
        'variable "keyring_name" {\n  type = string\n}\n\n'
        'variable "members" {\n  default = []\n}\n'
    ),
    "modules/kms/outputs.tf": (
        'output "keyring_id" {\n  value = google_kms_key_ring.this.id\n}\n'
    ),
    "modules/kms/main.tf": (
        'resource "google_kms_key_ring" "this" {\n  name = var.keyring_name\n}\n'
    ),
}


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


def _line(text: str, needle: str) -> int:
    return next(i for i, ln in enumerate(text.splitlines(), 1) if needle in ln)


@pytest.fixture(params=["duckdb", "sqlite"])
def repo(request, tmp_path, monkeypatch):
    if request.param == "duckdb":
        pytest.importorskip("duckdb")
    monkeypatch.setenv("CGH_DB", request.param)
    reset_connection()
    base = tmp_path.resolve()
    root = base / "infra"
    _write(root, REPO)
    _write(base / "tf-modules", MODULES)
    index_repo(root, method="os_walk")
    _srv._root = root
    _srv._conn = None
    yield root
    reset_connection()
    _srv._root = None
    _srv._conn = None


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


def _short(root: Path, path: str) -> str:
    return path.replace(f"{root.parent}/", "")


# ---------------------------------------------------------------------------
# Module inputs in name search
# ---------------------------------------------------------------------------


def test_name_search_lands_on_the_module_input(repo):
    tools = _tools()
    out = json.loads(tools["search_symbols"]("members", name_only=True))
    hits = {(r["kind"], r["name"], _short(repo, r["file"])) for r in out["results"]}
    assert ("tf_module_arg", "module.kms.members", "infra/env/main.tf") in hits
    # The variable it feeds, read from the mapped modules checkout.
    assert ("tf_var", "var.members", "tf-modules/modules/kms/variables.tf") in hits
    only = json.loads(tools["search_symbols"]("keyring", kinds="tf_module_arg"))
    assert {r["name"] for r in only["results"]} == {
        "module.kms.keyring_name",
        "module.reg.keyring_name",
    }
    # Meta-arguments (source, version) are not inputs.
    meta = json.loads(tools["search_symbols"]("version", kinds="tf_module_arg"))
    assert meta["results"] == []


def test_cgh_search_lists_terraform_blocks_and_inputs(repo, capsys):
    import argparse

    import codegraph.cli.commands_query as cq

    cq.cmd_search(
        argparse.Namespace(
            root=str(repo), query="members", text=None, limit=None, offset=0, json=True
        )
    )
    rows = json.loads(capsys.readouterr().out)["results"]
    assert {(r["kind"], r["name"]) for r in rows} >= {
        ("tf_module_arg", "module.kms.members"),
        ("tf_var", "var.members"),
    }


def test_lookup_of_an_input_name_and_its_line(repo):
    tools = _tools()
    out = json.loads(tools["symbol_lookup"]("module.kms.keyring_name"))
    [hit] = out["definitions"]
    line = _line(MAIN, '"acme-data-keys"')
    assert (hit["kind"], hit["lines"]) == ("tf_module_arg", f"{line}-{line}")
    by_name = json.loads(tools["symbol_lookup"]("keyring_name"))["definitions"]
    assert {(d["kind"], d["name"]) for d in by_name} >= {
        ("tf_module_arg", "module.kms.keyring_name"),
        ("tf_var", "var.keyring_name"),
    }


def test_string_values_stay_in_text_search(repo):
    tools = _tools()
    out = json.loads(tools["search_symbols"]("acme-data-keys"))
    assert out["results"] == []
    from codegraph.core.fts import fts_search, get_fts_conn

    hits = fts_search(get_fts_conn(repo), "acme-data-keys", limit=5)
    assert any(h.name == "module.kms.keyring_name" for h in hits)


# ---------------------------------------------------------------------------
# Remote module sources mapped to a local checkout
# ---------------------------------------------------------------------------


def test_mapped_remote_module_links_inputs_and_outputs(repo):
    tools = _tools()
    callees = json.loads(tools["find_callees"]("module.kms.keyring_name"))["callees"]
    assert {(c["callee"], _short(repo, c["file"])) for c in callees} == {
        ("var.keyring_name", "tf-modules/modules/kms/variables.tf")
    }
    # The ?ref= is ignored, the //subdir followed, a registry source mapped.
    mod = json.loads(tools["find_callees"]("module.reg"))["callees"]
    assert ("var.keyring_name", "tf-modules/modules/kms/variables.tf") in {
        (c["callee"], _short(repo, c["file"])) for c in mod
    }
    conn = get_connection(repo)
    users = {
        (c["caller"], _short(repo, c["file"]))
        for c in callers_of(conn, "output.keyring_id")
    }
    assert ("google_x.user", "infra/env/main.tf") in users


def test_mapped_checkout_is_read_never_indexed_or_written(repo):
    modules = repo.parent / "tf-modules"
    assert not (modules / ".codegraph").exists()
    assert sorted(
        str(p.relative_to(modules)) for p in modules.rglob("*") if p.is_file()
    ) == sorted(MODULES)
    conn = get_connection(repo)
    # Its blocks, resources included, but no File node for its files.
    assert not conn.find_nodes("File", contains={"path": "tf-modules"})
    resources = conn.find_nodes(
        "TFResource", contains={"file_path": "tf-modules"}, return_fields=["address"]
    )
    assert [r["address"] for r in resources] == ["google_kms_key_ring.this"]
    copies = conn.find_nodes(
        "TFVar", contains={"file_path": "tf-modules"}, return_fields=["address"]
    )
    assert sorted(r["address"] for r in copies) == [
        "output.keyring_id",
        "var.keyring_name",
        "var.members",
    ]


def test_module_mapped_inside_the_repo_links_like_a_local_one(repo):
    conn = get_connection(repo)
    cidr = conn.find_nodes("TFVar", where={"address": "var.cidr"}, return_fields=["id"])
    assert len(cidr) == 1  # the indexed file, no on-demand copy
    edges = conn.find_neighbors(
        "TF_REFS_VAR", src_where={"address": "module.net"}, return_dst=["id"]
    )
    assert {r["dst_id"] for r in edges} == {cidr[0]["id"]}


def test_mapped_module_change_is_seen_on_the_next_caller_index(repo):
    variables = repo.parent / "tf-modules/modules/kms/variables.tf"
    variables.write_text('variable "keyring_name" {}\n', encoding="utf-8")
    os.utime(variables, (1, 1))  # a different signature for sure
    main = repo / "env/main.tf"
    index_file(main, repo, force=True)
    conn = get_connection(repo)
    assert not conn.find_nodes("TFVar", where={"address": "var.members"})
    # Re-saving the caller keeps the links into the checkout.
    index_file(main, repo, force=True)
    assert conn.find_neighbors(
        "TF_REFS_VAR",
        src_where={"address": "module.kms.keyring_name"},
        return_dst=["id"],
    )


def test_source_resolution_rules(tmp_path):
    root = str(tmp_path / "repo")
    src = ModuleSources(
        root,
        {
            "git::https://github.com/acme/tf-modules.git": "../mods",
            "https://github.com/acme/tf-modules/special": "/abs/special",
            "acme/kms/google": "vendor/kms",
        },
        [root, str(tmp_path / "extra")],
        [os.path.join(root, "child")],
    )
    mods = os.path.normpath(os.path.join(root, "../mods"))
    assert src.resolve(root, "git::https://github.com/acme/tf-modules//kms?ref=v1") == (
        os.path.join(mods, "kms")
    )
    assert src.resolve(root, "github.com/x/y") == ""
    # A prefix only matches at a path boundary; the longest prefix wins.
    assert src.resolve(root, "git::https://github.com/acme/tf-modules-extra//x") == ""
    assert src.resolve(root, "https://github.com/acme/tf-modules/special//a") == (
        "/abs/special/a"
    )
    assert src.resolve(root, "acme/kms/google") == os.path.join(root, "vendor/kms")
    assert src.resolve(os.path.join(root, "env"), "../m") == os.path.join(root, "m")
    # Unmapped remote sources stay opaque.
    assert ModuleSources(root).resolve(root, "acme/kms/google") == ""
    assert src.is_indexed(os.path.join(root, "vendor/kms"))
    assert src.is_indexed(str(tmp_path / "extra/kms"))
    assert not src.is_indexed(os.path.join(root, "child/kms"))  # federated
    assert not src.is_indexed(mods)


# ---------------------------------------------------------------------------
# moved blocks
# ---------------------------------------------------------------------------


def test_moved_block_is_a_caller_of_the_module_it_names(repo):
    conn = get_connection(repo)
    callers = {c["caller"] for c in callers_of(conn, "module.kms")}
    assert "moved.module.kms.google_kms_key_ring.this" in callers


# ---------------------------------------------------------------------------
# Block-precise impact
# ---------------------------------------------------------------------------


def test_changed_entry_round_trip():
    assert split_changed_entry("env/a.tf#L3-5,9") == ("env/a.tf", [(3, 5), (9, 9)])
    assert split_changed_entry("env/a.tf") == ("env/a.tf", None)
    assert split_changed_entry("odd#Lname.tf") == ("odd#Lname.tf", None)
    assert format_changed_entry("env/a.tf", [(3, 5), (9, 9)]) == "env/a.tf#L3-5,9"
    assert format_changed_entry("env/a.tf", None) == "env/a.tf"


def test_one_line_change_impacts_only_the_referencing_files(repo):
    conn = get_connection(repo)
    line = _line(MAIN, '"acme-data-keys"')
    report = build_impact_report(conn, str(repo), [f"env/main.tf#L{line}"])
    assert report["since_changed"] == ["env/main.tf"]
    assert {s["name"] for s in report["changed_symbols"]} == {
        "module.kms",
        "module.kms.keyring_name",
    }
    # moved.tf names module.kms; third.tf uses google_x.user, which uses
    # module.kms. other.tf only uses google_y.base, which did not change.
    assert {r["file"] for r in report["impacted"]} == {
        "env/moved.tf",
        "env/third.tf",
    }
    assert {(r["file"], r["mentions"]) for r in report["related"]} == {
        ("contracts/env-vars.yaml", "module.kms")
    }
    # The same lines given as a mapping.
    again = build_impact_report(
        conn, str(repo), ["env/main.tf"], changed_lines={"env/main.tf": [(line, line)]}
    )
    assert again["impacted"] == report["impacted"]


def test_whole_file_change_still_covers_every_block(repo):
    conn = get_connection(repo)
    report = build_impact_report(conn, str(repo), ["env/main.tf"])
    assert {r["file"] for r in report["impacted"]} == {
        "env/moved.tf",
        "env/third.tf",
        "env/other.tf",
    }


def test_impact_of_a_tf_path_is_block_precise(repo):
    tools = _tools()
    out = json.loads(tools["impact_of"](str(repo / "env/third.tf")))
    assert out["impacted"] == []


def test_cli_reads_changed_lines_from_git(repo):
    from codegraph.cli.commands_impact import _changed_ranges, _git_changed_lines

    diff = (
        "diff --git a/env/main.tf b/env/main.tf\n"
        "--- a/env/main.tf\n+++ b/env/main.tf\n"
        "@@ -3 +3 @@\n-a\n+b\n"
        "@@ -10,2 +9,0 @@\n-x\n-y\n"
        "diff --git a/gone.tf b/gone.tf\n--- a/gone.tf\n+++ /dev/null\n"
        "@@ -1 +0,0 @@\n-z\n"
    )
    assert _changed_ranges(diff) == {"env/main.tf": [(3, 3), (9, 10)]}

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "base")
    main = repo / "env/main.tf"
    main.write_text(MAIN.replace("acme-data-keys", "acme-keys"), encoding="utf-8")
    git("commit", "-qam", "change")
    line = _line(MAIN, '"acme-data-keys"')
    assert _git_changed_lines(str(repo), "HEAD~1", ["env/main.tf", "README.md"]) == {
        "env/main.tf": [(line, line)]
    }
