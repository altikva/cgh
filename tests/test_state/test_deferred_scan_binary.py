# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Field fix from a Windows monorepo: scanner text never
#              carries embedded nulls (binary docx/xlsx decoded with
#              errors=replace used to poison subprocess argv).

from __future__ import annotations

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
