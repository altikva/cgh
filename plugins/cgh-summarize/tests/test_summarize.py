# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh-summarize tests: the egress gate in both postures, the
#              min_kb threshold, backend selection under the egress
#              constraint (fake backends, no network, no real CLIs), the
#              carry-forward drift policy, third-party backends through
#              the extension namespace, and corpus insights persisting to
#              the knowledge store.

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("cgh_summarize")

from cgh_summarize.backends import StructuralBackend, pick_backend
from cgh_summarize.gate import cloud_allowed, egress_posture
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


def _repo(tmp_path: Path, mode: str = "assist") -> Path:
    cg = tmp_path / ".codegraph"
    cg.mkdir(exist_ok=True)
    (cg / "config.toml").write_text(f'[codegraph]\nmode = "{mode}"\n', encoding="utf-8")
    return tmp_path


def _flag(root: Path, file: str, key: str, value: str = "1", severity: str = "warn"):
    store.record_findings(
        root, file, "test", [ScanFinding(key=key, value=value, severity=severity)]
    )


BIG = "x = 1  # padding line\n" * 400  # ~8 KB, above the 4 KB threshold


class TestGate:
    def test_clean_file_passes_in_assist(self, tmp_path):
        root = _repo(tmp_path)
        allowed, reason = cloud_allowed(root, "/r/a.py", {})
        assert allowed and reason == "gate clear"

    def test_confidential_blocks(self, tmp_path):
        root = _repo(tmp_path)
        _flag(root, "/r/a.py", "confidential", "true", "block")
        allowed, reason = cloud_allowed(root, "/r/a.py", {})
        assert not allowed and "confidential" in reason

    def test_block_severity_blocks(self, tmp_path):
        root = _repo(tmp_path)
        _flag(root, "/r/a.py", "secret.aws_key", "1", "block")
        assert not cloud_allowed(root, "/r/a.py", {})[0]

    def test_pii_blocks_unless_allowed(self, tmp_path):
        root = _repo(tmp_path)
        _flag(root, "/r/a.py", "pii.email", "2", "warn")
        assert not cloud_allowed(root, "/r/a.py", {})[0]
        assert cloud_allowed(root, "/r/a.py", {"allow_pii": True})[0]

    def test_strict_requires_explicit_label(self, tmp_path):
        root = _repo(tmp_path, mode="secure")
        assert egress_posture(root, {}) == "strict"
        assert not cloud_allowed(root, "/r/a.py", {})[0]
        _flag(root, "/r/a.py", "confidential", "false", "info")
        assert cloud_allowed(root, "/r/a.py", {})[0]

    def test_explicit_egress_key_wins_over_mode(self, tmp_path):
        root = _repo(tmp_path, mode="secure")
        assert egress_posture(root, {"egress": "open"}) == "open"


class TestBackendSelection:
    def test_cloud_excluded_when_gate_denies(self):
        cloud, local = FakeCloud(), FakeLocal()
        picked = pick_backend({}, extras=[cloud, local], cloud_allowed=False)
        assert picked is local

    def test_explicit_backend_name(self):
        cloud, local = FakeCloud(), FakeLocal()
        picked = pick_backend(
            {"backend": "fake-local"}, extras=[cloud, local], cloud_allowed=True
        )
        assert picked is local

    def test_structural_always_available(self):
        assert pick_backend({"backend": "structural"}, cloud_allowed=False) is not None


class TestScanner:
    def test_small_file_skipped(self, tmp_path):
        root = _repo(tmp_path)
        s = SummarizeScanner({}, root, extras_fn=lambda: [FakeCloud()])
        assert s.scan(Path("/r/small.py"), "tiny\n", None) == []

    def test_big_clean_file_uses_cloud(self, tmp_path):
        root = _repo(tmp_path)
        cloud = FakeCloud()
        s = SummarizeScanner({}, root, extras_fn=lambda: [cloud])
        found = s.scan(Path("/r/big.py"), BIG, None)
        keys = {f.key: f.value for f in found}
        assert keys["summary"] == "cloud summary"
        assert cloud.calls and "big.py" in cloud.calls[0]

    def test_flagged_file_falls_back_to_local(self, tmp_path):
        root = _repo(tmp_path)
        _flag(root, "/r/big.py", "pii.email", "1", "warn")
        cloud, local = FakeCloud(), FakeLocal()
        s = SummarizeScanner({}, root, extras_fn=lambda: [cloud, local])
        found = s.scan(Path("/r/big.py"), BIG, None)
        assert {f.key: f.value for f in found}["summary"] == "local summary"
        assert cloud.calls == []

    def test_carry_forward_under_drift_threshold(self, tmp_path):
        root = _repo(tmp_path)
        cloud = FakeCloud()
        s = SummarizeScanner({}, root, extras_fn=lambda: [cloud])
        first = s.scan(Path("/r/big.py"), BIG, None)
        store.record_findings(root, "/r/big.py", s.name, first)

        # 10% more lines: under the 30% threshold, summary carried.
        grown = BIG + ("y = 2\n" * 40)
        second = s.scan(Path("/r/big.py"), grown, None)
        assert {f.key: f.value for f in second}["summary"] == "cloud summary"
        assert len(cloud.calls) == 1  # no second model call

        # 50% more lines: re-summarized.
        blown = BIG + ("y = 2\n" * 200)
        third = s.scan(Path("/r/big.py"), blown, None)
        assert len(cloud.calls) == 2
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
        store.record_findings(
            root,
            "/r/secret.py",
            "summarize",
            [ScanFinding(key="summary", value="hidden")],
        )
        _flag(root, "/r/secret.py", "confidential", "true", "block")

        recorded = {}

        def fake_knowledge_record(title, body, kind, tags, repo_root=None, **kw):
            recorded.update(title=title, body=body, kind=kind, tags=tags)
            return 42

        import codegraph.state.call_log as call_log

        monkeypatch.setattr(call_log, "knowledge_record", fake_knowledge_record)

        cloud = FakeCloud(reply="the donation flow is duplicated")
        result = run_insights(root, {}, extras_fn=lambda: [cloud])

        assert result["knowledge_id"] == 42
        assert result["files"] == 1  # secret.py withheld
        assert result["excluded"] == 1
        assert "donations" in cloud.calls[0]
        assert "hidden" not in cloud.calls[0]
        assert recorded["kind"] == "pattern"


class TestCliCommandShapes:
    def _argv(self, tool: str, monkeypatch) -> list[str]:
        import cgh_summarize.backends as backends
        from cgh_summarize.backends import CliBackend

        monkeypatch.setattr(backends.shutil, "which", lambda t: f"/usr/bin/{t}")
        return CliBackend(tool)._command("hello", {})

    def test_bob_headless_prompt(self, monkeypatch):
        argv = self._argv("bob", monkeypatch)
        assert argv == ["/usr/bin/bob", "-p", "hello"]

    def test_codex_uses_exec(self, monkeypatch):
        argv = self._argv("codex", monkeypatch)
        assert argv == ["/usr/bin/codex", "exec", "hello"]

    def test_bob_in_auto_selection_order(self):
        from cgh_summarize.backends import _BUILTINS

        names = [b.name for b in _BUILTINS]
        assert "cli:bob" in names
        # After the other CLIs, before the local daemon fallbacks.
        assert names.index("cli:codex") < names.index("cli:bob") < names.index("ollama")
