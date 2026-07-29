# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Field fixes from a Windows monorepo: scanner text never
#              carries embedded nulls (binary docx/xlsx decoded with
#              errors=replace used to poison subprocess argv), and the
#              summarize CLI backends run npm .cmd shims through cmd /c
#              instead of raising WinError 2 blamed on the scanned file.

from __future__ import annotations

from pathlib import Path

import pytest

import codegraph.state.deferred_scan as deferred
from codegraph.plugin_api import ScanFinding
from codegraph.state import findings as store


@pytest.fixture(autouse=True)
def clean_state():
    store.reset_for_tests()
    yield
    store.reset_for_tests()


class TestNullStripping:
    def test_deferred_scanner_never_sees_nulls(self, tmp_path, monkeypatch):
        (tmp_path / ".codegraph").mkdir()
        binary = tmp_path / "doc.docx"
        binary.write_bytes(b"PK\x03\x04\x00\x00fake zip\x00payload")

        seen: list[str] = []

        class Probe:
            name = "probe"
            deferred = True

            def scan(self, path, text, index):
                seen.append(text)
                return [ScanFinding(key="probe.ok", value="1")]

        monkeypatch.setattr("codegraph.plugins.scanners", lambda: [("test", Probe())])
        deferred._process(str(tmp_path), str(binary), "sha1")

        assert len(seen) == 1
        assert "\x00" not in seen[0]
        assert "fake zip" in seen[0]
        rows = store.query_findings(tmp_path, key_prefix="probe.")
        assert len(rows) == 1


class TestCliBackendWindowsShim:
    def _backend_calls(self, monkeypatch, which_result: str):
        pytest.importorskip("cgh_summarize")
        import cgh_summarize.backends as backends

        calls: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            from types import SimpleNamespace

            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="ok summary", stderr="")

        monkeypatch.setattr(backends.shutil, "which", lambda tool: which_result)
        monkeypatch.setattr(backends.subprocess, "run", fake_run)
        return backends.CliBackend("claude"), calls

    def test_cmd_shim_runs_through_cmd_slash_c(self, monkeypatch):
        backend, calls = self._backend_calls(
            monkeypatch, r"C:\Users\x\AppData\Roaming\npm\claude.CMD"
        )
        assert backend.available({}) is True
        assert backend.summarize("hello", {}) == "ok summary"
        assert calls[0][:2] == ["cmd", "/c"]
        assert calls[0][2].lower().endswith("claude.cmd")

    def test_plain_binary_runs_directly(self, monkeypatch):
        backend, calls = self._backend_calls(monkeypatch, "/usr/local/bin/claude")
        backend.summarize("hello", {})
        assert calls[0][0] == "/usr/local/bin/claude"
        assert calls[0][1] == "-p"

    def test_prompt_nulls_stripped_defensively(self, monkeypatch):
        backend, calls = self._backend_calls(monkeypatch, "/usr/local/bin/claude")
        backend.summarize("bad\x00prompt", {})
        assert "\x00" not in calls[0][2]


class TestBinaryExcerpt:
    def test_replacement_soup_uses_section_previews(self, tmp_path, monkeypatch):
        pytest.importorskip("cgh_summarize")
        from cgh_summarize.scanner import build_prompt

        class FakeIdx:
            functions: list = []
            classes: list = []
            resources: list = []

            class _Sec:
                level = 1
                title = "Overview"
                body_preview = "the actual document text"

            sections = [_Sec()]

        class FakeParser:
            def parse(self, path):
                return FakeIdx()

        monkeypatch.setattr(
            "codegraph.parsers.get_parser_for_path", lambda p: FakeParser()
        )
        soup = "\ufffd" * 300 + "PK zip noise"
        prompt = build_prompt(Path("doc.docx"), soup, "en")

        assert "the actual document text" in prompt
        assert "PK zip noise" not in prompt
        assert "# Overview" in prompt


class TestBackendErrorContext:
    def test_scanner_names_the_failing_backend(self, tmp_path, monkeypatch):
        pytest.importorskip("cgh_summarize")
        from cgh_summarize.scanner import SummarizeScanner

        (tmp_path / ".codegraph").mkdir()

        class Broken:
            name = "cli:claude"
            egress = "local"

            def available(self, config):
                return True

            def summarize(self, prompt, config):
                raise FileNotFoundError("[WinError 2] file not found")

        scanner = SummarizeScanner({}, tmp_path, extras_fn=lambda: [Broken()])
        big = "x = 1\n" * 2000
        with pytest.raises(RuntimeError, match=r"summarize backend cli:claude"):
            scanner.scan(Path("/r/big.py"), big, None)
