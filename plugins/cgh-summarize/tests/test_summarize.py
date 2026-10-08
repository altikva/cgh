# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh-summarize tests: no index-time registration, the
#              min_kb threshold, local-only backend selection (fake
#              backends, no network), the carry-forward drift policy,
#              third-party backends through the extension namespace, and
#              corpus insights persisting to the knowledge store.

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("cgh_summarize")

from cgh_summarize.backends import StructuralBackend, pick_backend
from cgh_summarize.scanner import SummarizeScanner

from codegraph.plugin_api import ScanFinding
from codegraph.state import findings as store


@pytest.fixture(autouse=True)
def clean_store():
    store.reset_for_tests()
    yield
    store.reset_for_tests()


class FakeCloud:
    name = "fake-cloud"
    egress = "cloud"

    def __init__(self, reply="cloud summary"):
        self.reply = reply
        self.calls: list[str] = []

    def available(self, config):
        return True

    def summarize(self, prompt, config):
        self.calls.append(prompt)
        return self.reply


class FakeLocal(FakeCloud):
    name = "fake-local"
    egress = "local"

    def __init__(self):
        super().__init__(reply="local summary")


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".codegraph").mkdir(exist_ok=True)
    return tmp_path


BIG = "x = 1  # padding line\n" * 400  # ~8 KB, above the 4 KB threshold


class TestRegistration:
    def test_no_scanner_registered(self, tmp_path):
        import cgh_summarize

        import codegraph.plugins as plugins
        from codegraph.plugin_api import PluginAPI

        plugins._reset_for_tests()
        try:
            api = PluginAPI("summarize", tmp_path, {}, plugins._registries)
            cgh_summarize.register(api)
            assert set(api.surfaces) == {"cli", "mcp"}
            assert plugins._registries.scanners == []
        finally:
            plugins._reset_for_tests()


class TestBackendSelection:
    def test_cloud_never_picked(self):
        cloud, local = FakeCloud(), FakeLocal()
        assert pick_backend({}, extras=[cloud, local]) is local
        # The 0.2 keyword is still accepted and has no effect.
        assert pick_backend({}, extras=[cloud, local], cloud_allowed=True) is local

    def test_explicit_cloud_backend_is_refused(self):
        picked = pick_backend({"backend": "fake-cloud"}, extras=[FakeCloud()])
        assert picked is None

    def test_explicit_backend_name(self):
        cloud, local = FakeCloud(), FakeLocal()
        picked = pick_backend({"backend": "fake-local"}, extras=[cloud, local])
        assert picked is local

    def test_builtins_are_local_only(self):
        from cgh_summarize.backends import _BUILTINS

        assert [b.name for b in _BUILTINS] == ["ollama", "openai", "structural"]

    def test_structural_always_available(self):
        assert pick_backend({"backend": "structural"}, cloud_allowed=False) is not None


class TestOllamaEgressClass:
    """The "local" label is earned by the URL, not declared: a remote
    ollama_url or openai_base_url reclassifies the backend as cloud, and
    a cloud backend is never picked."""

    def _ollama(self):
        from cgh_summarize.backends import OllamaBackend

        return OllamaBackend()

    def test_loopback_url_is_local(self):
        assert self._ollama().egress_class({}) == "local"
        assert (
            self._ollama().egress_class({"ollama_url": "http://localhost:11434"})
            == "local"
        )

    def test_remote_url_is_cloud(self):
        cfg = {"ollama_url": "http://192.168.1.20:11434"}
        assert self._ollama().egress_class(cfg) == "cloud"

    def test_remote_ollama_is_never_picked(self, monkeypatch):
        """An ollama on a remote host is excluded even though its static
        egress attribute still reads "local"."""
        remote = {"backend": "ollama", "ollama_url": "http://192.168.1.20:11434"}
        ollama = self._ollama()
        monkeypatch.setattr(type(ollama), "available", lambda self, config: True)
        assert pick_backend(remote, extras=[]) is None

    def test_openai_backend_local_only_on_loopback(self):
        from cgh_summarize.backends import OpenAICompatibleBackend

        b = OpenAICompatibleBackend()
        assert b.egress_class({"openai_base_url": "http://127.0.0.1:8080/v1"}) == (
            "local"
        )
        assert b.egress_class({"openai_base_url": "https://api.example.com/v1"}) == (
            "cloud"
        )
        remote = {
            "backend": "openai",
            "openai_base_url": "https://api.example.com/v1",
            "openai_model": "m",
        }
        assert pick_backend(remote) is None

    def test_unparsable_url_probe_says_unavailable(self):
        assert self._ollama().available({"ollama_url": "http://"}) is False


class TestScanner:
    def test_rootless_scanner_stays_silent(self):
        from cgh_summarize.scanner import SummarizeScanner

        big = "x" * 8192
        found = SummarizeScanner({}, None).scan(Path("/r/a.py"), big, None)
        assert found == []

    def test_small_file_skipped(self, tmp_path):
        root = _repo(tmp_path)
        s = SummarizeScanner({}, root, extras_fn=lambda: [FakeLocal()])
        assert s.scan(Path("/r/small.py"), "tiny\n", None) == []

    def test_big_file_uses_local_backend_never_cloud(self, tmp_path):
        root = _repo(tmp_path)
        cloud, local = FakeCloud(), FakeLocal()
        s = SummarizeScanner({}, root, extras_fn=lambda: [cloud, local])
        found = s.scan(Path("/r/big.py"), BIG, None)
        assert {f.key: f.value for f in found}["summary"] == "local summary"
        assert local.calls and "big.py" in local.calls[0]
        assert cloud.calls == []

    def test_carry_forward_under_drift_threshold(self, tmp_path):
        root = _repo(tmp_path)
        local = FakeLocal()
        s = SummarizeScanner({}, root, extras_fn=lambda: [local])
        first = s.scan(Path("/r/big.py"), BIG, None)
        store.record_findings(root, "/r/big.py", s.name, first)

        # 10% more lines: under the 30% threshold, summary carried.
        grown = BIG + ("y = 2\n" * 40)
        second = s.scan(Path("/r/big.py"), grown, None)
        assert {f.key: f.value for f in second}["summary"] == "local summary"
        assert len(local.calls) == 1  # no second model call

        # 50% more lines: re-summarized.
        blown = BIG + ("y = 2\n" * 200)
        third = s.scan(Path("/r/big.py"), blown, None)
        assert len(local.calls) == 2
        assert {f.key for f in third} == {"summary", "summary.meta"}

    def test_structural_backend_returns_outline(self):
        prompt = "header\nOUTLINE:\nfn add\nclass Api\nEXCERPT:\nboring text"
        out = StructuralBackend().summarize(prompt, {})
        assert "fn add" in out and "boring text" not in out


class TestInsights:
    def test_insights_persist_to_knowledge(self, tmp_path, monkeypatch):
        from cgh_summarize.insights import run_insights

        root = _repo(tmp_path)
        store.record_findings(
            root,
            "/r/a.py",
            "summarize",
            [
                ScanFinding(key="summary", value="handles donations"),
                ScanFinding(key="summary.meta", value="{}"),
            ],
        )

        recorded = {}

        def fake_knowledge_record(title, body, kind, tags, repo_root=None, **kw):
            recorded.update(title=title, body=body, kind=kind, tags=tags)
            return 42

        # Patch the plugin_api facade, not codegraph.state.call_log:
        # the facade caches its lazy re-exports on first resolution, so
        # a patch on the source module is invisible once any earlier
        # test resolved the name through plugin_api.
        import codegraph.plugin_api as api

        monkeypatch.setattr(api, "knowledge_record", fake_knowledge_record)

        cloud = FakeCloud(reply="never")
        local = FakeLocal()
        local.reply = "the donation flow is duplicated"
        result = run_insights(root, {}, extras_fn=lambda: [cloud, local])

        assert result["knowledge_id"] == 42
        assert result["files"] == 1
        assert result["excluded"] == 0
        assert result["backend"] == "fake-local"
        assert "donations" in local.calls[0]
        assert cloud.calls == []
        assert recorded["kind"] == "pattern"


def test_resolve_and_gate_on_installed_model(monkeypatch):
    """Ollama backend auto-picks an installed model and reads as
    unavailable when nothing is pulled (so the scanner degrades, not 404s)."""
    from cgh_summarize import backends as sb

    u = "http://127.0.0.1:11434"
    sb.reset_tags_cache()
    sb._tags_cache[u] = (9e18, frozenset({"gemma3:4b", "qwen2.5vl:3b"}))
    assert sb.resolve_ollama_model(u, "gemma3:4b") == "gemma3:4b"  # configured
    assert sb.resolve_ollama_model(u, "qwen2.5:1.5b") == "qwen2.5vl:3b"  # auto qwen
    # daemon reachable but no model -> available() is False
    sb._tags_cache[u] = (9e18, frozenset())
    monkeypatch.setattr(sb.socket, "create_connection", lambda *a, **k: _Dummy())
    assert sb.OllamaBackend().available({"ollama_url": u}) is False


class _Dummy:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
