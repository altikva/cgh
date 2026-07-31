# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Regex PII scanner tests: every pattern, Luhn and mod-97
#              validation rejecting random digit runs, counts instead of
#              raw values, key disabling, and the end-to-end path through
#              index_repo into the finding store.

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

        api = PluginAPI("pii", tmp_path, {}, plugins._registries)
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

        api = PluginAPI("pii", tmp_path, {"ner": True}, plugins._registries)
        cgh_pii.register(api)
        # Regex tier registered; NER either registered (extra installed)
        # or skipped with a stderr note, never an exception.
        names = [s.name for _, s in plugins._registries.scanners]
        assert "pii-regex" in names
