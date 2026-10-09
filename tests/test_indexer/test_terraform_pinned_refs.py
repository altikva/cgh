# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Remote Terraform modules pinned with ?ref= and mapped by
#              [terraform] module_sources to a git checkout: each ref is read
#              from git at that commit (never checked out), into a directory
#              of its own; a missing ref falls back to the working tree with
#              a notice; the module's resources are read and reachable as
#              module.m.<address>; and a change to the mapping or to what a
#              pinned ref resolves to re-parses the Terraform files, on both
#              backends.

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from codegraph.analysis import terraform as tf
from codegraph.analysis.callers import callers_of
from codegraph.core.config import load_config
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import incremental_reindex, index_repo, module_sources_changed
from codegraph.state.scan_meta import read_meta

MAPPING = (
    "[terraform]\n"
    'module_sources = { "git::https://example.com/acme/mods" = "../mods" }\n'
)

MAIN = (
    'module "kms" {\n'
    '  source  = "git::https://example.com/acme/mods.git//kms?ref=v1"\n'
    "  keyring = 1\n}\n\n"
    'module "kms_new" {\n'
    '  source = "git::https://example.com/acme/mods.git//kms?ref=v2"\n'
    "  ring   = 2\n}\n\n"
    'module "lost" {\n'
    '  source = "git::https://example.com/acme/mods.git//kms?ref=v404"\n'
    "  ring   = 3\n}\n"
)

V1 = {
    "kms/variables.tf": 'variable "keyring" {}\n',
    "kms/main.tf": (
        'resource "google_kms_key_ring" "r" {\n  name = var.keyring\n}\n\n'
        'resource "google_kms_key_ring_iam_member" "encrypter" {\n'
        "  key_ring_id = google_kms_key_ring.r.id\n"
        '  role        = "roles/cloudkms.cryptoKeyEncrypterDecrypter"\n}\n'
    ),
}
V2 = {
    "kms/variables.tf": 'variable "ring" {}\n',
    "kms/main.tf": 'resource "google_kms_key_ring" "r" {\n  name = var.ring\n}\n',
}


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _commit(path: Path, message: str) -> None:
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", message)


@pytest.fixture(params=["duckdb", "sqlite"])
def repo(request, tmp_path, monkeypatch):
    if request.param == "duckdb":
        pytest.importorskip("duckdb")
    monkeypatch.setenv("CGH_DB", request.param)
    reset_connection()
    base = tmp_path.resolve()
    mods = base / "mods"
    mods.mkdir()
    _git(mods, "init", "-q")
    for tag, files in (("v1", V1), ("v2", V2)):
        _write(mods, files)
        _commit(mods, tag)
        _git(mods, "tag", tag)
    root = base / "infra"
    _write(root, {"env/main.tf": MAIN, ".codegraph/config.toml": MAPPING})
    _git(root, "init", "-q")
    _commit(root, "init")
    index_repo(root, method="os_walk")
    yield root
    reset_connection()


def _var_targets(root: Path, src: str) -> set[str]:
    rows = get_connection(root).find_neighbors(
        "TF_REFS_VAR", src_where={"address": src}, return_dst=["id"]
    )
    return {r["dst_id"].replace(f"{root.parent}/", "") for r in rows}


def test_each_ref_links_to_that_version(repo):
    assert _var_targets(repo, "module.kms.keyring") == {
        "mods/kms@v1/variables.tf::var.keyring"
    }
    assert _var_targets(repo, "module.kms_new.ring") == {
        "mods/kms@v2/variables.tf::var.ring"
    }
    # The checkout lacks v404: the working tree (at v2) answers.
    assert _var_targets(repo, "module.lost.ring") == {"mods/kms/variables.tf::var.ring"}


def test_module_repo_is_never_written(repo):
    mods = repo.parent / "mods"
    assert _git(mods, "status", "--porcelain") == ""
    assert not (mods / ".codegraph").exists()
    assert "v2" in _git(mods, "log", "-1", "--format=%s")


def test_module_resources_are_read_and_reachable(repo):
    conn = get_connection(repo)
    hits = tf.lookup(conn, "google_kms_key_ring_iam_member.encrypter")
    assert [h["file"].replace(f"{repo.parent}/", "") for h in hits] == [
        "mods/kms@v1/main.tf"
    ]
    inside = tf.lookup(conn, "module.kms.google_kms_key_ring_iam_member.encrypter")
    assert [h["start_line"] for h in inside] == [5]
    # References inside the module are linked.
    users = {r["caller"] for r in callers_of(conn, "module.kms.google_kms_key_ring.r")}
    assert users == {"google_kms_key_ring_iam_member.encrypter"}
    # No File node for the module files.
    assert not conn.find_nodes("File", contains={"path": "/mods/"})


def test_report_and_notice_name_the_missing_ref(repo):
    state = read_meta(repo)["module_sources"]
    [mapping] = state["mappings"]
    assert sorted(mapping["refs_found"]) == ["v1", "v2"]
    assert mapping["refs_missing"] == {"v404": ["kms"]}
    assert mapping["git"] is True
    [notice] = tf.module_sources_notices(state)
    assert "v404" in notice and "kms" in notice


def test_status_and_doctor_show_module_sources(repo, capsys):
    from codegraph.cli.commands_monitor import cmd_doctor, cmd_status

    cmd_status(argparse.Namespace(root=str(repo), json=True, workers=False))
    payload = json.loads(capsys.readouterr().out)
    [mapping] = payload["module_sources"]["mappings"]
    assert mapping["refs_missing"] == {"v404": ["kms"]}
    assert any("v404" in n for n in payload["notices"])
    try:
        cmd_doctor(argparse.Namespace(root=str(repo), owner=False, strict=False))
    except SystemExit:
        pass
    out = capsys.readouterr().out
    assert "module_sources:" in out and "v404" in out


def test_a_ref_appearing_reparses_terraform(repo):
    mods = repo.parent / "mods"
    assert not module_sources_changed(repo)
    _git(mods, "tag", "v404", "v1")
    assert module_sources_changed(repo)
    result = incremental_reindex(repo)
    assert result["mode"] == "fallback_full"
    assert not module_sources_changed(repo)
    # v404 is v1 now: the call resolves there, and v1 has no var.ring.
    [lost] = get_connection(repo).find_nodes(
        "TFResource", where={"address": "module.lost"}, return_fields=["source_dir"]
    )
    assert lost["source_dir"].endswith("/mods/kms@v404")
    assert _var_targets(repo, "module.lost.ring") == set()
    rows = get_connection(repo).find_nodes(
        "TFVar", contains={"file_path": "/mods/kms/"}, return_fields=["file_path"]
    )
    # The working-tree copy read for the missing ref is gone.
    assert rows == []


def test_a_moved_branch_and_an_edited_mapping_are_seen(repo):
    mods = repo.parent / "mods"
    main = repo / "env/main.tf"
    main.write_text(MAIN.replace("?ref=v2", "?ref=main"), encoding="utf-8")
    _git(mods, "branch", "-M", "main")
    index_repo(repo, method="os_walk")
    assert _var_targets(repo, "module.kms_new.ring") == {
        "mods/kms@main/variables.tf::var.ring"
    }
    _write(mods, {"kms/variables.tf": 'variable "ring" {}\nvariable "x" {}\n'})
    _commit(mods, "more")
    assert module_sources_changed(repo)
    index_repo(repo, method="os_walk")
    assert not module_sources_changed(repo)
    conn = get_connection(repo)
    assert conn.find_nodes(
        "TFVar", where={"address": "var.x"}, return_fields=["file_path"]
    )

    (repo / ".codegraph/config.toml").write_text("", encoding="utf-8")
    assert module_sources_changed(repo, load_config(repo))
    index_repo(repo, method="os_walk")
    assert not conn.find_nodes("TFVar", contains={"file_path": "/mods/"})
    assert "module_sources" not in read_meta(repo)


def test_owner_start_reindexes_on_a_changed_mapping(repo):
    from codegraph.server import _startup_index_needed

    assert not _startup_index_needed(repo, reindex=False)
    (repo / ".codegraph/config.toml").write_text("", encoding="utf-8")
    assert _startup_index_needed(repo, reindex=False)


def test_option_like_refs_never_reach_git(tmp_path):
    assert tf.resolve_ref(str(tmp_path), "--output=/tmp/x") == ""
    assert tf.resolve_ref(str(tmp_path), "a..b") == ""
    assert tf._source_ref("git::https://x//m?depth=1&ref=v1.2") == "v1.2"
