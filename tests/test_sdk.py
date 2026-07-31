# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The embedding surface: scan_text over installed
#              scanners, the pure egress gate matrix, caller-keyed
#              pseudonymization, capability errors naming the package,
#              the in-memory store semantics, and summarize through the
#              structural backend (no network).

from __future__ import annotations

import pytest

import codegraph.plugins as plugins
from codegraph import sdk
from codegraph.plugin_api import ScanFinding


@pytest.fixture(autouse=True)
def fresh_plugins():
    plugins._reset_for_tests()
    yield
    plugins._reset_for_tests()


class TestScanText:
    def test_pii_scanner_runs_on_provided_text(self):
        pytest.importorskip("cgh_pii")
        findings = sdk.scan_text(
            "contact me at joy@example.com", path="note.txt", scanners=["pii"]
        )
        assert any(f.key == "pii.email" for f in findings)

    def test_unknown_scanner_raises_named_capability(self):
        with pytest.raises(sdk.CapabilityMissing) as exc:
            sdk.scan_text("hello", scanners=["nonexistent"])
        assert "cgh-nonexistent" in str(exc.value)


class TestEgressDecision:
    def test_block_severity_denies(self):
        v = sdk.egress_decision(
            [ScanFinding(key="secret.aws_key", value="x", severity="block")],
            mode="assist",
        )
        assert not v and "block" in v.reason

    def test_confidential_true_denies(self):
        v = sdk.egress_decision(
            [ScanFinding(key="confidential", value="true")], mode="assist"
        )
        assert not v

    def test_pii_denies_unless_allowed(self):
        pii = [ScanFinding(key="pii.email", value="a@b.c", severity="warn")]
        assert not sdk.egress_decision(pii, mode="assist")
        assert sdk.egress_decision(pii, mode="assist", allow_pii=True)

    def test_secure_is_an_allowlist(self):
        assert not sdk.egress_decision([], mode="secure")
        assert sdk.egress_decision([], mode="secure", labeled_non_confidential=True)

    def test_clean_assist_allows(self):
        assert sdk.egress_decision([], mode="assist")


class TestPseudonymize:
    def test_stable_and_distinct(self):
        key = b"0" * 32
        a1 = sdk.pseudonymize("pii.email", "a@x.com", key)
        a2 = sdk.pseudonymize("pii.email", "a@x.com", key)
        b = sdk.pseudonymize("pii.email", "b@x.com", key)
        assert a1 == a2 != b
        assert a1.startswith("<pii.email:")

    def test_different_secret_different_pseudonym(self):
        p1 = sdk.pseudonymize("pii.email", "a@x.com", b"0" * 32)
        p2 = sdk.pseudonymize("pii.email", "a@x.com", b"1" * 32)
        assert p1 != p2

    def test_short_secret_rejected(self):
        with pytest.raises(ValueError):
            sdk.pseudonymize("pii.email", "a@x.com", b"short")


class TestSummarize:
    def test_structural_backend_no_network(self):
        pytest.importorskip("cgh_summarize")
        out = sdk.summarize(
            "def add(a, b):\n    return a + b\n", config={"backend": "structural"}
        )
        assert isinstance(out, str)

    def test_missing_package_raises(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake(name, *a, **k):
            if name.startswith("cgh_summarize"):
                raise ImportError(name)
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake)
        with pytest.raises(sdk.CapabilityMissing) as exc:
            sdk.summarize("text")
        assert "cgh-summarize" in str(exc.value)


class TestVisionCapability:
    def test_missing_package_raises_named_capability(self, monkeypatch):
        import builtins
        import sys

        monkeypatch.delitem(sys.modules, "cgh_vision", raising=False)
        real_import = builtins.__import__

        def fake(name, *a, **k):
            if name.startswith("cgh_vision"):
                raise ImportError(name)
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake)
        with pytest.raises(sdk.CapabilityMissing) as exc:
            sdk.image_inventory("x.png")
        assert "cgh-vision" in str(exc.value)


class TestInMemoryStore:
    def test_replace_per_scanner_and_query(self):
        store = sdk.InMemoryFindingStore()
        store.record("a.py", "pii", [ScanFinding(key="pii.email", value="x")], "sha1")
        store.record(
            "a.py",
            "pii",
            [ScanFinding(key="pii.phone", value="y", severity="warn")],
            "sha2",
        )
        found = store.query(key_prefix="pii.")
        assert [f.key for f in found] == ["pii.phone"]
        assert store.already_scanned("a.py", "pii", "sha2")
        assert not store.already_scanned("a.py", "pii", "sha3")

    def test_query_filters(self):
        store = sdk.InMemoryFindingStore()
        store.record(
            "b.py",
            "secrets",
            [ScanFinding(key="secret.k", value="v", severity="block")],
        )
        assert store.query(severity="block")
        assert not store.query(severity="warn")
        assert store.query(path="b.py") and not store.query(path="c.py")
