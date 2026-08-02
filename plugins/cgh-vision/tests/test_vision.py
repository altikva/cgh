# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Vision plugin tests with a scripted backend (no Ollama):
#              inventory vocabulary filtering, the router's cost model
#              (a logo runs one call), diagram extraction end to end
#              with post-processing (dedup, arrow-annotation drop,
#              identity separation), the bare-array edge reply, the
#              scanner's finding keys including pii.image_identity, and
#              the SDK wiring through codegraph.sdk.image_*.

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("cgh_vision")

import cgh_vision.pipeline as pipeline
from cgh_vision.pipeline import (
    extract_diagram,
    inventory,
    postprocess,
    route,
    split_identities,
)


class ScriptedBackend:
    """Returns canned replies in order; records prompts."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def __call__(self, model, path, prompt, config=None, timeout_s=120):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else "{}"


def _script(monkeypatch, replies: list[str]) -> ScriptedBackend:
    backend = ScriptedBackend(replies)
    monkeypatch.setattr(pipeline, "ask", backend)
    return backend


IMG = Path("/img/x.png")


class TestInventory:
    def test_vocabulary_is_enforced(self, monkeypatch):
        _script(
            monkeypatch,
            [
                '{"summary": "a chart", "content": ["chart", "alien_type"], "text_density": "sparse"}'
            ],
        )
        inv = inventory(IMG, {})
        assert inv["content"] == ["chart"]
        assert inv["summary"] == "a chart"

    def test_garbage_reply_falls_back_to_other(self, monkeypatch):
        _script(monkeypatch, ["not json at all"])
        assert inventory(IMG, {})["content"] == ["other"]


class TestRouter:
    def test_logo_costs_one_call(self, monkeypatch):
        backend = _script(
            monkeypatch,
            ['{"summary": "a logo", "content": ["logo"], "text_density": "none"}'],
        )
        _inv, md = route(IMG, {})
        assert len(backend.prompts) == 1
        assert "a logo" in md and "Components" not in md

    def test_diagram_routes_to_extraction(self, monkeypatch):
        backend = _script(
            monkeypatch,
            [
                '{"summary": "an archi", "content": ["architecture_diagram"], "text_density": "sparse"}',
                '{"nodes": ["API", "DB"], "edges": [["API", "DB"]], "zones": []}',
                '{"title": "Archi", "kinds": {"DB": "database"}, "tech": {}}',
                '[{"source": "API", "target": "DB", "label": "SQL"}]',
            ],
        )
        _inv, md = route(IMG, {})
        assert len(backend.prompts) == 4
        assert "```mermaid" in md and 'API -- "SQL" --> DB' in md.replace("  ", " ")


class TestExtraction:
    def test_end_to_end_with_postprocessing(self, monkeypatch):
        _script(
            monkeypatch,
            [
                '{"nodes": ["API Server", "api server", "Bastion 10.128.0.5", "HTTPS"],'
                ' "edges": [["API Server", "Bastion 10.128.0.5"]], "zones": []}',
                '{"title": "T", "kinds": {"API Server": "service"}, "tech": {}}',
                '{"edges": [{"source": "Bastion 10.128.0.5", "target": "API Server", "label": "HTTPS"}]}',
            ],
        )
        ex = extract_diagram(IMG, {})
        labels = [n["label"] for n in ex["nodes"]]
        assert labels == ["API Server", "Bastion"]  # dedup + identity split
        assert ex["nodes"][1]["identities"] == ["10.128.0.5"]
        assert len(ex["edges"]) == 1  # reversed duplicate deduped
        assert "flowchart LR" in ex["mermaid"]
        assert ex["title"] == "T"

    def test_fast_profile_is_single_call(self, monkeypatch):
        backend = _script(
            monkeypatch,
            ['{"nodes": ["A"], "edges": [], "zones": []}'],
        )
        ex = extract_diagram(IMG, {"profile": "fast"})
        assert len(backend.prompts) == 1
        assert [n["label"] for n in ex["nodes"]] == ["A"]


class TestSplitIdentities:
    def test_shapes(self):
        assert split_identities("Bastion 10.128.0.5") == ("Bastion", ["10.128.0.5"])
        assert split_identities("LDAP ldap.acme.internal") == (
            "LDAP",
            ["ldap.acme.internal"],
        )
        assert split_identities("Cloud Run") == ("Cloud Run", [])


class TestPostprocessLegend:
    def test_schema_echo_filtered(self):
        out = postprocess(
            {
                "nodes": ["A"],
                "edges": [],
                "zones": [],
                "legend": [
                    {"symbol": "...", "meaning": "..."},
                    {"symbol": "red box", "meaning": "critical path"},
                ],
            }
        )
        assert out["legend"] == [{"symbol": "red box", "meaning": "critical path"}]


class TestScanner:
    def _scan(self, tmp_path, monkeypatch, replies):
        from cgh_vision.scanner import VisionScanner

        img = tmp_path / "d.png"
        img.write_bytes(b"\x89PNG" + b"0" * 6000)
        monkeypatch.setattr("cgh_vision.scanner.available", lambda cfg: True)
        _script(monkeypatch, replies)
        return VisionScanner({}, tmp_path).scan(img, "", None)

    def test_diagram_findings_and_identities(self, tmp_path, monkeypatch):
        found = self._scan(
            tmp_path,
            monkeypatch,
            [
                '{"summary": "an archi", "content": ["architecture_diagram"], "text_density": "sparse"}',
                '{"nodes": ["Bastion 10.128.0.5"], "edges": [], "zones": []}',
                '{"title": "", "kinds": {}, "tech": {}}',
                '{"edges": []}',
            ],
        )
        keys = {f.key for f in found}
        assert {
            "image.content",
            "image.summary",
            "diagram.mermaid",
            "diagram.entities",
        } <= keys
        idents = [f for f in found if f.key == "pii.image_identity"]
        assert [f.value for f in idents] == ["10.128.0.5"]
        entities = json.loads(
            next(f.value for f in found if f.key == "diagram.entities")
        )
        assert entities["nodes"][0]["label"] == "Bastion"

    def test_non_image_and_small_files_skip(self, tmp_path, monkeypatch):
        from cgh_vision.scanner import VisionScanner

        scanner = VisionScanner({}, tmp_path)
        assert scanner.scan(tmp_path / "a.py", "code", None) == []
        tiny = tmp_path / "icon.png"
        tiny.write_bytes(b"x" * 100)
        assert scanner.scan(tiny, "", None) == []

    def test_backend_down_raises(self, tmp_path, monkeypatch):
        from cgh_vision.scanner import VisionScanner

        img = tmp_path / "d.png"
        img.write_bytes(b"\x89PNG" + b"0" * 6000)
        monkeypatch.setattr("cgh_vision.scanner.available", lambda cfg: False)
        with pytest.raises(RuntimeError, match="Ollama"):
            VisionScanner({}, tmp_path).scan(img, "", None)


class TestEgressPosture:
    """Image bytes only ever reach a loopback daemon in secure mode; a
    remote ollama_url is refused there and audited elsewhere."""

    def _image(self, tmp_path):
        img = tmp_path / "d.png"
        img.write_bytes(b"\x89PNG" + b"0" * 6000)
        return img

    def _mode(self, monkeypatch, mode):
        import codegraph.plugin_api as api

        class _Cfg:
            pass

        cfg = _Cfg()
        cfg.mode = mode
        monkeypatch.setattr(api, "load_config", lambda root: cfg)

    def test_secure_refuses_remote_url(self, tmp_path, monkeypatch):
        from cgh_vision.backends import VisionError
        from cgh_vision.scanner import VisionScanner

        self._mode(monkeypatch, "secure")
        scanner = VisionScanner({"ollama_url": "http://192.168.1.20:11434"}, tmp_path)
        with pytest.raises(VisionError, match="non-loopback"):
            scanner.scan(self._image(tmp_path), "", None)

    def test_mode_probe_failure_refuses(self, tmp_path, monkeypatch):
        """Unknown mode is secure mode: the probe fails closed."""
        from cgh_vision.backends import VisionError
        from cgh_vision.scanner import VisionScanner

        import codegraph.plugin_api as api

        def boom(root):
            raise OSError("unreadable config")

        monkeypatch.setattr(api, "load_config", boom)
        scanner = VisionScanner({"ollama_url": "http://192.168.1.20:11434"}, tmp_path)
        with pytest.raises(VisionError, match="non-loopback"):
            scanner.scan(self._image(tmp_path), "", None)

    def test_assist_proceeds_and_audits(self, tmp_path, monkeypatch):
        from cgh_vision.scanner import VisionScanner

        self._mode(monkeypatch, "assist")
        audited = []
        monkeypatch.setattr("cgh_vision.scanner.available", lambda cfg: True)
        _script(
            monkeypatch,
            ['{"summary": "a photo", "content": ["photo"], "text_density": "none"}'],
        )
        scanner = VisionScanner({"ollama_url": "http://192.168.1.20:11434"}, tmp_path)
        monkeypatch.setattr(
            type(scanner), "_audit", lambda self, message: audited.append(message)
        )
        found = scanner.scan(self._image(tmp_path), "", None)
        assert {f.key for f in found} == {"image.content", "image.summary"}
        assert audited and "non-loopback" in audited[0]


class TestSdkWiring:
    def test_sdk_image_functions_reach_the_plugin(self, monkeypatch):
        from codegraph import sdk

        _script(
            monkeypatch,
            ['{"summary": "a chart", "content": ["chart"], "text_density": "none"}'],
        )
        inv = sdk.image_inventory(IMG, {})
        assert inv["content"] == ["chart"]
