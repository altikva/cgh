# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Secure-at-rest guarantees: in secure mode, pii.* and
#              secret.* finding values are replaced at write time by
#              keyed one-way pseudonyms (stable per repo, irreversible,
#              raw value never on disk), and the guard denies direct
#              agent access to .codegraph/ (Read/Grep/Bash), pointing
#              at the MCP tools instead. Assist mode changes nothing.

from __future__ import annotations

import re

import pytest

from codegraph.plugin_api import ScanFinding
from codegraph.state import findings as store
from codegraph.state.guard import check_bash, check_tool_call, sync_bobignore


@pytest.fixture(autouse=True)
def clean_state():
    store.reset_for_tests()
    yield
    store.reset_for_tests()


def _repo(tmp_path, mode="secure"):
    (tmp_path / ".codegraph").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".codegraph" / "config.toml").write_text(
        f'[codegraph]\nmode = "{mode}"\n', encoding="utf-8"
    )
    return tmp_path


PSEUDO = re.compile(r"^<pii\.email:[0-9a-f]{10}>$")


class TestPseudonymizationAtRest:
    def test_pii_value_never_reaches_disk_in_secure(self, tmp_path):
        root = _repo(tmp_path)
        store.record_findings(
            root,
            "/r/a.py",
            "pii",
            [ScanFinding(key="pii.email", value="joy@altikva.com", severity="warn")],
        )
        rows = store.query_findings(root, key_prefix="pii.")
        assert len(rows) == 1
        assert PSEUDO.match(rows[0]["value"])
        # The raw value is nowhere in the SQLite file itself.
        blob = store.findings_db_path(root).read_bytes()
        assert b"joy@altikva.com" not in blob

    def test_pseudonyms_are_stable_and_distinct(self, tmp_path):
        root = _repo(tmp_path)
        a1 = store.pseudonymize(root, "pii.email", "a@x.com")
        a2 = store.pseudonymize(root, "pii.email", "a@x.com")
        b = store.pseudonymize(root, "pii.email", "b@x.com")
        assert a1 == a2 != b

    def test_already_pseudonymized_value_is_untouched(self, tmp_path):
        root = _repo(tmp_path)
        once = store.pseudonymize(root, "secret.aws_key", "AKIA123")
        store.record_findings(
            root,
            "/r/b.py",
            "secrets",
            [ScanFinding(key="secret.aws_key", value=once, severity="block")],
        )
        rows = store.query_findings(root, key_prefix="secret.")
        assert rows[0]["value"] == once

    def test_non_sensitive_keys_stay_raw(self, tmp_path):
        root = _repo(tmp_path)
        store.record_findings(
            root,
            "/r/c.py",
            "summarize",
            [
                ScanFinding(key="summary", value="plain prose"),
                ScanFinding(key="confidential", value="true", severity="block"),
            ],
        )
        values = {r["key"]: r["value"] for r in store.query_findings(root)}
        assert values["summary"] == "plain prose"
        assert values["confidential"] == "true"

    def test_assist_mode_stores_raw(self, tmp_path):
        root = _repo(tmp_path, mode="assist")
        store.record_findings(
            root,
            "/r/a.py",
            "pii",
            [ScanFinding(key="pii.email", value="joy@altikva.com", severity="warn")],
        )
        rows = store.query_findings(root, key_prefix="pii.")
        assert rows[0]["value"] == "joy@altikva.com"

    def test_key_file_is_created_once(self, tmp_path):
        root = _repo(tmp_path)
        store.pseudonymize(root, "pii.email", "x@y.z")
        key_path = root / ".codegraph" / "pseudo.key"
        first = key_path.read_text()
        store.pseudonymize(root, "pii.email", "other@y.z")
        assert key_path.read_text() == first


class TestIndexAccessDenied:
    def test_read_of_findings_db_denied_in_secure(self, tmp_path):
        root = _repo(tmp_path)
        reason = check_tool_call(
            root,
            "Read",
            {"file_path": str(root / ".codegraph" / "findings.db")},
            "secure",
        )
        assert reason and "MCP" in reason

    def test_grep_inside_index_denied_in_secure(self, tmp_path):
        root = _repo(tmp_path)
        reason = check_tool_call(
            root, "Grep", {"path": str(root / ".codegraph")}, "secure"
        )
        assert reason and "MCP" in reason

    def test_bash_touching_index_denied_in_secure(self, tmp_path):
        root = _repo(tmp_path)
        reason = check_bash(
            root,
            "sqlite3 .codegraph/findings.db 'select value from findings'",
            "secure",
        )
        assert reason and "MCP" in reason

    def test_assist_mode_leaves_index_readable(self, tmp_path):
        root = _repo(tmp_path, mode="assist")
        assert (
            check_tool_call(
                root,
                "Read",
                {"file_path": str(root / ".codegraph" / "findings.db")},
                "assist",
            )
            is None
        )
        assert check_bash(root, "cat .codegraph/config.toml", "assist") is None

    def test_regular_files_unaffected_in_secure(self, tmp_path):
        root = _repo(tmp_path)
        assert (
            check_tool_call(
                root, "Read", {"file_path": str(root / "main.py")}, "secure"
            )
            is None
        )

    def test_bobignore_covers_the_index(self, tmp_path):
        root = _repo(tmp_path)
        sync_bobignore(root)
        content = (root / ".bobignore").read_text(encoding="utf-8")
        assert ".codegraph/" in content
