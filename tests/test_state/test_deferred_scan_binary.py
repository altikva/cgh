# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Field fixes from a Windows monorepo: scanner text never
#              carries embedded nulls (binary docx/xlsx decoded with
#              errors=replace used to poison subprocess argv), binary
#              documents summarize from their section previews, and a
#              failing summarize backend is named in the error instead of
#              the scanned file.

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
            name = "broken-local"
            egress = "local"

            def available(self, config):
                return True

            def summarize(self, prompt, config):
                raise FileNotFoundError("[WinError 2] file not found")

        scanner = SummarizeScanner({}, tmp_path, extras_fn=lambda: [Broken()])
        big = "x = 1\n" * 2000
        with pytest.raises(RuntimeError, match=r"summarize backend broken-local"):
            scanner.scan(Path("/r/big.py"), big, None)
