# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Regex PII scanner tests: every pattern, Luhn and mod-97
#              validation rejecting random digit runs, counts instead of
#              raw values, key disabling, the secrets-only mode, the
#              opt-in index-time registration, `cgh pii scan`, and the
#              end-to-end path through index_repo into the finding store.

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytest.importorskip("cgh_pii")

from cgh_pii.regex_scanner import RegexPiiScanner

import codegraph.plugins as plugins


def _scan(text: str, disabled=None):
    return RegexPiiScanner(disabled_keys=disabled or set()).scan(
        Path("x.txt"), text, None
    )


def _keys(text: str) -> dict[str, object]:
    return {f.key: f for f in _scan(text)}


class TestPatterns:
    def test_email(self):
        found = _keys("contact: joy.ndjama@altikva.com et sales@ex.co\n")
        assert found["pii.email"].value == "2"
        assert found["pii.email"].line == 1
        # The value never contains the matched address.
        assert "altikva" not in found["pii.email"].value

    def test_phone_international(self):
        assert "pii.phone" in _keys("call +33 6 12 34 56 78 today")
        assert "pii.phone" not in _keys("version 1.2.3.4 build 5678")

    def test_iban_mod97(self):
        # Valid French IBAN test number.
        assert "pii.iban" in _keys("rib: FR1420041010050500013M02606")
        # Same shape, corrupted check digits: rejected.
        assert "pii.iban" not in _keys("rib: FR9920041010050500013M02606")

    def test_card_luhn(self):
        assert "pii.card" in _keys("card 4111 1111 1111 1111 exp 12/28")
        assert "pii.card" not in _keys("card 4111 1111 1111 1112 exp 12/28")
        # A long build number is not a card.
        assert "pii.card" not in _keys("build 1234567890123456789012")

    def test_secrets(self):
        found = _keys(
            "AWS_KEY=AKIAIOSFODNN7EXAMPLE\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
            'password = "hunter2hunter2"\n'
        )
        assert found["secret.aws_key"].severity == "block"
        assert found["secret.private_key"].severity == "block"
        assert found["secret.assignment"].severity == "warn"

    def test_clean_text(self):
        assert _scan("def add(a, b):\n    return a + b\n") == []

    def test_disable_keys(self):
        found = _keys("joy@altikva.com")
        assert "pii.email" in found
        assert "pii.email" not in {
            f.key for f in _scan("joy@altikva.com", disabled={"pii.email"})
        }


class TestEndToEnd:
    @pytest.fixture(autouse=True)
    def clean_registries(self):
        plugins._reset_for_tests()
        from codegraph.state import findings as store

        store.reset_for_tests()
        yield
        plugins._reset_for_tests()
        store.reset_for_tests()

    def test_indexed_repo_records_pii_findings(self, tmp_path):
        import cgh_pii

        from codegraph.plugin_api import PluginAPI

        api = PluginAPI(
            "pii", tmp_path, {"scan_on_index": True, "pii": True}, plugins._registries
        )
        cgh_pii.register(api)

        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        (tmp_path / "clean.py").write_text("def ok():\n    return 1\n")
        (tmp_path / "leaky.py").write_text(
            "SUPPORT = 'joy@altikva.com'\nKEY = 'AKIAIOSFODNN7EXAMPLE'\n"
        )

        from codegraph.core.db import reset_connection
        from codegraph.indexer import index_repo

        reset_connection()
        try:
            index_repo(str(tmp_path))
        finally:
            reset_connection()

        from codegraph.state.findings import query_findings

        rows = query_findings(tmp_path)
        by_key = {r["key"]: r for r in rows}
        assert by_key["pii.email"]["file"].endswith("leaky.py")
        assert by_key["secret.aws_key"]["severity"] == "block"
        assert all(r["file"].endswith("leaky.py") for r in rows)

    def test_ner_absent_is_a_clean_skip(self, tmp_path, capsys):
        import cgh_pii

        from codegraph.plugin_api import PluginAPI

        api = PluginAPI(
            "pii", tmp_path, {"scan_on_index": True, "ner": True}, plugins._registries
        )
        cgh_pii.register(api)
        # Regex tier registered; NER either registered (extra installed)
        # or skipped with a stderr note, never an exception.
        names = [s.name for _, s in plugins._registries.scanners]
        assert "pii-regex" in names


class TestSecretsOnly:
    def test_pii_false_keeps_only_secrets(self):
        text = "joy@altikva.com\nKEY = 'AKIAIOSFODNN7EXAMPLE'\n"
        keys = {f.key for f in RegexPiiScanner(pii=False).scan(Path("x"), text, None)}
        assert keys == {"secret.aws_key"}


class TestRegistration:
    @pytest.fixture(autouse=True)
    def clean_registries(self):
        plugins._reset_for_tests()
        yield
        plugins._reset_for_tests()

    def _register(self, tmp_path, config):
        import cgh_pii

        from codegraph.plugin_api import PluginAPI

        api = PluginAPI("pii", tmp_path, config, plugins._registries)
        cgh_pii.register(api)
        return api

    def test_default_registers_no_index_scanner(self, tmp_path):
        api = self._register(tmp_path, {})
        assert set(api.surfaces) == {"cli", "extensions"}
        assert plugins._registries.scanners == []

    def test_sdk_scan_text_keeps_pii_patterns(self, tmp_path, monkeypatch):
        """sdk.scan_text(scanners=["pii"]) is served by the on-demand
        scanner and still reports PII, as before 0.4.0."""
        from types import SimpleNamespace

        import cgh_pii

        from codegraph import sdk

        monkeypatch.setattr(
            plugins,
            "_iter_entry_points",
            lambda: [SimpleNamespace(name="pii", load=lambda: cgh_pii)],
        )
        found = sdk.scan_text("mail joy@altikva.com", scanners=["pii"])
        assert [f.key for f in found] == ["pii.email"]

    def test_scan_on_index_is_secrets_only_by_default(self, tmp_path):
        self._register(tmp_path, {"scan_on_index": True})
        (_, scanner), *_ = plugins._registries.scanners
        found = {f.key for f in scanner.scan(Path("x"), "joy@altikva.com\n", None)}
        assert found == set()

    def test_scan_on_index_with_pii(self, tmp_path):
        self._register(tmp_path, {"scan_on_index": True, "pii": True})
        (_, scanner), *_ = plugins._registries.scanners
        found = {f.key for f in scanner.scan(Path("x"), "joy@altikva.com\n", None)}
        assert found == {"pii.email"}


class TestScanCommand:
    def _run(self, argv, config=None):
        import argparse

        from cgh_pii.cli import make_cli_registrar

        ap = argparse.ArgumentParser()
        sub = ap.add_subparsers(dest="cmd")
        make_cli_registrar(config or {})(sub)
        args = ap.parse_args(["pii", *argv])
        try:
            args.func(args)
        except SystemExit as exc:
            return exc.code
        return 0

    def _tree(self, tmp_path):
        (tmp_path / "ok.py").write_text("def f():\n    return 1\n")
        (tmp_path / "contact.md").write_text("mail joy@altikva.com\n")
        return tmp_path

    def test_clean_tree_exits_zero(self, tmp_path, capsys):
        root = self._tree(tmp_path)
        assert self._run(["scan", str(root)]) == 0
        assert capsys.readouterr().out == ""

    def test_pii_flag_reports_but_does_not_fail(self, tmp_path, capsys):
        root = self._tree(tmp_path)
        assert self._run(["scan", str(root), "--pii"]) == 0
        out = capsys.readouterr().out
        assert "contact.md:1" in out and "pii.email" in out
        assert "altikva" not in out.split("contact.md")[1]

    def test_block_secret_exits_one(self, tmp_path, capsys):
        root = self._tree(tmp_path)
        (root / "deploy.pem").write_text("-----BEGIN RSA PRIVATE KEY-----\nx\n")
        assert self._run(["scan", str(root)]) == 1
        assert "secret.private_key" in capsys.readouterr().out

    def test_git_ignored_files_are_skipped(self, tmp_path):
        root = self._tree(tmp_path)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        (root / ".gitignore").write_text("*.pem\n")
        (root / "deploy.pem").write_text("-----BEGIN RSA PRIVATE KEY-----\nx\n")
        assert self._run(["scan", str(root)]) == 0

    def test_json_output(self, tmp_path, capsys):
        import json

        f = tmp_path / "k.env"
        f.write_text("AWS=AKIAIOSFODNN7EXAMPLE\n")
        assert self._run(["scan", str(f), "--json"]) == 1
        hits = json.loads(capsys.readouterr().out)
        assert hits == [
            {
                "path": str(f),
                "line": 1,
                "key": "secret.aws_key",
                "severity": "block",
                "count": 1,
            }
        ]

    def test_missing_path_exits_two(self, tmp_path):
        assert self._run(["scan", str(tmp_path / "nope")]) == 2

    def test_redact_still_takes_one_file(self, tmp_path, capsys):
        f = tmp_path / "n.txt"
        f.write_text("mail joy@altikva.com\n")
        assert self._run(["redact", str(f), "--only", "email"]) == 0
        assert "joy@altikva.com" not in capsys.readouterr().out
