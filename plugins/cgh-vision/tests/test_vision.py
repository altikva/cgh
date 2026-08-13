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
    render_markdown,
    route,
    route_structured,
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
                # 3 nodes with an edge: a healthy read, so the fallback
                # reader stays out of the picture (see TestFallbackReader).
                '{"nodes": ["API", "DB", "Cache"], "edges": [["API", "DB"]],'
                ' "zones": []}',
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


class TestRouteStructured:
    _REPLIES = [
        '{"summary": "an archi", "content": ["architecture_diagram"], "text_density": "sparse"}',
        '{"nodes": ["API", "DB"], "edges": [["API", "DB"]], "zones": []}',
        '{"title": "Archi", "kinds": {}, "tech": {}}',
        '{"edges": []}',
    ]

    def test_structured_shape(self, monkeypatch):
        _script(monkeypatch, list(self._REPLIES))
        result = route_structured(IMG, {})
        assert result["image"] == "x.png"
        assert result["inventory"]["content"] == ["architecture_diagram"]
        assert [n["label"] for n in result["diagram"]["nodes"]] == ["API", "DB"]
        assert result["tables"] == [] and result["charts"] == []
        assert result["text"] is None

    def test_markdown_projection_matches_route(self, monkeypatch):
        _script(monkeypatch, list(self._REPLIES))
        result = route_structured(IMG, {})
        _script(monkeypatch, list(self._REPLIES))
        _inv, md = route(IMG, {})
        assert render_markdown(result) == md


def _role(prompt: str) -> str:
    """Which pass a prompt belongs to. The three share an opening
    sentence, so classify on what only one of them says."""
    if '"nodes": ["label"' in prompt:
        return "structure"
    if '"kinds"' in prompt:
        return "enrich"
    if "listing every drawn arrow" in prompt:
        return "edges"
    return "other"


class TestFallbackReader:
    """The default pair runs first; a second reader gets one attempt
    only when the result is skeletal, and only wins if it found more."""

    THIN = [
        '{"nodes": ["A", "B"], "edges": [], "zones": []}',  # skeletal
        '{"title": "", "kinds": {}, "tech": {}}',
        '{"edges": []}',
    ]
    RICH = [
        '{"nodes": ["A", "B", "C", "D"], "edges": [["A", "B"], ["C", "D"]],'
        ' "zones": []}',
        '{"title": "", "kinds": {}, "tech": {}}',
        '{"edges": []}',
    ]
    HEALTHY = [
        '{"nodes": ["A", "B", "C"], "edges": [["A", "B"]], "zones": []}',
        '{"title": "", "kinds": {}, "tech": {}}',
        '{"edges": []}',
    ]

    def _models(self, monkeypatch, script):
        """Records (model, role) per call. Role matters more than the
        model name now that the fallback reader IS the arrow reader:
        only a structure prompt sent to a model other than the primary
        one is a fallback attempt."""
        seen: list[tuple[str, str]] = []
        replies = list(script)

        def spy(model, path, prompt, config=None, timeout_s=120):
            seen.append((model, _role(prompt)))
            return replies.pop(0) if replies else "{}"

        monkeypatch.setattr(pipeline, "ask", spy)
        return seen

    @staticmethod
    def _fallback_fired(seen) -> bool:
        """A second structure read, by definition."""
        return sum(1 for _m, role in seen if role == "structure") > 1

    def test_skeletal_triggers_the_second_reader(self, monkeypatch):
        seen = self._models(monkeypatch, self.THIN + self.RICH)
        ex = extract_diagram(IMG, {})
        assert self._fallback_fired(seen)
        assert ("gemma3:4b", "structure") in seen  # by the arrow model
        assert [n["label"] for n in ex["nodes"]] == ["A", "B", "C", "D"]

    def test_healthy_result_never_pays_the_second_read(self, monkeypatch):
        seen = self._models(monkeypatch, self.HEALTHY)
        extract_diagram(IMG, {})
        assert not self._fallback_fired(seen)
        assert len(seen) == 3  # structure, enrich, edges. Nothing more.

    def test_poorer_retry_is_discarded(self, monkeypatch):
        poorer = [
            '{"nodes": ["Z"], "edges": [], "zones": []}',
            '{"title": "", "kinds": {}, "tech": {}}',
            '{"edges": []}',
        ]
        self._models(monkeypatch, self.THIN + poorer)
        ex = extract_diagram(IMG, {})
        assert [n["label"] for n in ex["nodes"]] == ["A", "B"]  # first kept

    def test_failing_second_read_degrades_silently(self, monkeypatch):
        """The retry can only ever add: a failure keeps the first
        result, it never propagates."""
        replies = list(self.THIN)
        calls = {"structure": 0}

        def spy(model, path, prompt, config=None, timeout_s=120):
            if _role(prompt) == "structure":
                calls["structure"] += 1
                if calls["structure"] > 1:  # the fallback attempt
                    raise RuntimeError("model not found")
            return replies.pop(0) if replies else "{}"

        monkeypatch.setattr(pipeline, "ask", spy)
        ex = extract_diagram(IMG, {})
        assert [n["label"] for n in ex["nodes"]] == ["A", "B"]
        assert calls["structure"] == 2  # it was attempted

    def test_config_disables_it(self, monkeypatch):
        seen = self._models(monkeypatch, self.THIN)
        extract_diagram(IMG, {"fallback_model": ""})
        assert not self._fallback_fired(seen)

    def test_fast_profile_never_falls_back(self, monkeypatch):
        seen = self._models(monkeypatch, ['{"nodes": ["A"], "edges": [], "zones": []}'])
        extract_diagram(IMG, {"profile": "fast"})
        assert seen == [("qwen2.5vl:3b", "structure")]

    def test_progress_announces_the_second_read(self, monkeypatch):
        self._models(monkeypatch, self.THIN + self.RICH)
        steps: list[str] = []
        extract_diagram(IMG, {}, progress=steps.append)
        assert any("second read" in s for s in steps)


class TestPostprocessZones:
    def test_nested_list_zone_becomes_a_label(self):
        out = postprocess({"nodes": ["A"], "edges": [], "zones": [["Cluster GKE"], []]})
        assert [z["label"] for z in out["zones"]] == ["Cluster GKE"]

    def test_dict_zone_keeps_members(self):
        out = postprocess(
            {"nodes": ["A"], "edges": [], "zones": [{"label": "Z", "members": ["A"]}]}
        )
        assert out["zones"] == [{"label": "Z", "members": ["A"]}]


class TestProgress:
    def test_route_announces_each_model_pass(self, monkeypatch):
        _script(
            monkeypatch,
            [
                '{"summary": "an archi", "content": ["architecture_diagram"], "text_density": "sparse"}',
                '{"nodes": ["API", "DB", "Cache"], "edges": [["API", "DB"]],'
                ' "zones": []}',
                '{"title": "", "kinds": {}, "tech": {}}',
                '{"edges": []}',
            ],
        )
        steps: list[str] = []
        route(IMG, {}, progress=steps.append)
        assert len(steps) == 4  # inventory, structure, enrich, arrows
        assert "inventory" in steps[0] and "arrows" in steps[-1]

    def test_sdk_stays_silent_without_observer(self, monkeypatch):
        _script(
            monkeypatch,
            ['{"summary": "a logo", "content": ["logo"], "text_density": "none"}'],
        )
        inv, _md = route(IMG, {})  # no progress kwarg: must not raise
        assert inv["content"] == ["logo"]


class TestPrescale:
    def _png(self, tmp_path, size: int):
        from PIL import Image

        p = tmp_path / f"img{size}.png"
        Image.new("RGB", (size, size), "white").save(p)
        return p

    def test_small_image_upscaled_2x_and_cleaned_up(self, tmp_path):
        from cgh_vision.pipeline import prescaled
        from PIL import Image

        p = self._png(tmp_path, 300)
        out, cleanup = prescaled(p, {})
        try:
            assert out != p
            with Image.open(out) as im:
                assert (im.width, im.height) == (600, 600)
        finally:
            cleanup()
        assert not out.exists()

    def test_large_image_untouched(self, tmp_path):
        from cgh_vision.pipeline import prescaled

        p = self._png(tmp_path, 1200)
        out, _cleanup = prescaled(p, {})
        assert out == p

    def test_disabled_by_config(self, tmp_path):
        from cgh_vision.pipeline import prescaled

        p = self._png(tmp_path, 300)
        out, _cleanup = prescaled(p, {"prescale": False})
        assert out == p

    def test_extraction_reads_the_scaled_copy(self, tmp_path, monkeypatch):
        seen: list = []

        def spy(model, path, prompt, config=None, timeout_s=120):
            seen.append(Path(path))
            return '{"nodes": [], "edges": [], "zones": []}'

        monkeypatch.setattr(pipeline, "ask", spy)
        src = self._png(tmp_path, 300)
        extract_diagram(src, {})
        assert seen and seen[0] != src  # the model saw the upscaled copy
        assert not seen[0].exists()  # and it was cleaned up after


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
        with pytest.raises(RuntimeError, match="not reachable"):
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


class TestMissingModels:
    """Naming the absent model beats failing on it a minute later, and
    a locally registered GGUF must count as present."""

    def _tags(self, monkeypatch, names):
        import io
        import json as _json
        from contextlib import contextmanager

        import cgh_vision.backends as backends

        @contextmanager
        def fake_urlopen(url, timeout=2.0):
            payload = _json.dumps({"models": [{"name": n} for n in names]}).encode()
            yield io.BytesIO(payload)

        monkeypatch.setattr(backends.urllib.request, "urlopen", fake_urlopen)

    def test_reports_only_what_is_absent(self, monkeypatch):
        from cgh_vision.backends import missing_models

        self._tags(monkeypatch, ["qwen2.5vl:3b"])
        assert missing_models({}, ["qwen2.5vl:3b", "gemma3:4b"]) == ["gemma3:4b"]

    def test_locally_registered_model_counts_as_present(self, monkeypatch):
        from cgh_vision.backends import missing_models

        self._tags(monkeypatch, ["qwen2.5-vl:7b"])  # ollama create from a GGUF
        assert missing_models({}, ["qwen2.5-vl:7b"]) == []

    def test_unreachable_daemon_raises_no_false_alarm(self, monkeypatch):
        import cgh_vision.backends as backends
        from cgh_vision.backends import missing_models

        def boom(*a, **k):
            raise OSError("connection refused")

        monkeypatch.setattr(backends.urllib.request, "urlopen", boom)
        assert missing_models({}, ["anything:1b"]) == []


class TestOllamaSetup:
    """cgh points at the publisher's own installer, never bundles or
    obfuscates it, and never auto-runs a piped remote script."""

    def test_windows_uses_winget(self):
        from cgh_vision.setup_ollama import official_install

        hint, argv = official_install("nt")
        assert argv == ["winget", "install", "--id", "Ollama.Ollama", "-e"]
        assert "ollama.com/install.sh" not in hint  # no pipe-to-shell on Windows

    def test_macos_uses_brew(self, monkeypatch):
        import cgh_vision.setup_ollama as m

        monkeypatch.setattr(m.sys, "platform", "darwin")
        _hint, argv = m.official_install("posix")
        assert argv == ["brew", "install", "ollama"]

    def test_linux_shows_the_script_but_never_runs_it(self, monkeypatch):
        import cgh_vision.setup_ollama as m

        monkeypatch.setattr(m.sys, "platform", "linux")
        hint, argv = m.official_install("posix")
        assert argv is None  # nothing auto-run
        assert "install.sh" in hint

    def test_offer_is_a_noop_without_a_tty(self, monkeypatch):
        import io

        import cgh_vision.setup_ollama as m
        from rich.console import Console

        monkeypatch.setattr(m.sys, "stdin", io.StringIO("y\n"))  # not a tty
        ran = m.offer_to_install(Console(file=io.StringIO()))
        assert ran is False

    def test_offer_never_runs_the_linux_script(self, monkeypatch):
        import cgh_vision.setup_ollama as m
        from rich.console import Console

        monkeypatch.setattr(m.sys, "platform", "linux")

        class _TTY:
            def isatty(self):
                return True

        monkeypatch.setattr(m.sys, "stdin", _TTY())
        called = {"run": False}
        monkeypatch.setattr(
            m.subprocess, "run", lambda *a, **k: called.__setitem__("run", True)
        )
        assert m.offer_to_install(Console()) is False
        assert called["run"] is False


class TestOpenAIBackend:
    """openai_base_url switches the transport to /chat/completions, so
    a local llama-server on GGUF needs no Ollama at all, and a gateway
    is reachable through the same path (subject to the egress gate)."""

    CFG = {"openai_base_url": "http://127.0.0.1:8080/v1"}

    def test_backend_kind_selected_by_config(self):
        from cgh_vision.backends import backend_kind

        assert backend_kind({}) == "ollama"
        assert backend_kind(self.CFG) == "openai"
        assert backend_kind({"vision_backend": "ollama", **self.CFG}) == "ollama"

    def test_loopback_endpoint_stays_local(self):
        from cgh_vision.backends import is_local

        assert is_local(self.CFG) is True
        assert is_local({"openai_base_url": "https://gw.corp/v1"}) is False

    def test_ask_posts_chat_completions_with_image(self, tmp_path, monkeypatch):
        import io

        import cgh_vision.backends as b

        img = tmp_path / "d.png"
        img.write_bytes(b"\x89PNG" + b"0" * 100)
        captured = {}

        def fake_urlopen(req, timeout=120):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode())
            payload = {"choices": [{"message": {"content": '{"nodes": []}'}}]}
            return io.BytesIO(json.dumps(payload).encode())

        monkeypatch.setattr(b.urllib.request, "urlopen", fake_urlopen)
        out = b.ask("qwen2.5-vl:7b", img, "read this", self.CFG)
        assert out == '{"nodes": []}'
        assert captured["url"] == "http://127.0.0.1:8080/v1/chat/completions"
        content = captured["body"]["messages"][0]["content"]
        assert content[0]["text"] == "read this"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert captured["body"]["temperature"] == 0

    def test_http_error_becomes_vision_error(self, tmp_path, monkeypatch):
        import cgh_vision.backends as b
        from cgh_vision.backends import VisionError

        img = tmp_path / "d.png"
        img.write_bytes(b"\x89PNG" + b"0" * 100)

        def boom(req, timeout=120):
            raise b.urllib.error.HTTPError(req.full_url, 404, "no model", {}, None)

        monkeypatch.setattr(b.urllib.request, "urlopen", boom)
        with pytest.raises(VisionError, match="404"):
            b.ask("x", img, "p", self.CFG)

    def test_missing_models_unknown_never_false_alarms(self, monkeypatch):
        import cgh_vision.backends as b

        monkeypatch.setattr(b, "installed_models", lambda cfg, timeout_s=2.0: set())
        assert b.missing_models(self.CFG, ["anything"]) == []


class TestSetupLlamacpp:
    """cgh vision setup --llamacpp wires the OpenAI backend at a local
    llama-server, installs only through the official channel, and never
    supervises the process."""

    def test_config_block_points_at_llama_server(self):
        from cgh_vision.setup_llamacpp import _config_block

        b = _config_block(8080)
        assert 'openai_base_url = "http://127.0.0.1:8080/v1"' in b
        assert 'nodes_model = "qwen2.5-vl"' in b
        assert 'fallback_model = ""' in b

    def test_macos_install_is_brew(self, monkeypatch):
        import cgh_vision.setup_llamacpp as m

        monkeypatch.setattr(m.sys, "platform", "darwin")
        _hint, argv = m.llamacpp_install("posix")
        assert argv == ["brew", "install", "llama.cpp"]

    def test_windows_points_at_signed_release_no_autorun(self):
        from cgh_vision.setup_llamacpp import llamacpp_install

        hint, argv = llamacpp_install("nt")
        assert argv is None  # no auto-run on Windows
        assert "github.com/ggml-org/llama.cpp/releases" in hint

    def test_writes_block_when_no_plugin_vision_section(self, tmp_path):
        from cgh_vision.setup_llamacpp import _write_config
        from rich.console import Console

        cfg = tmp_path / ".codegraph" / "config.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("[codegraph]\nmode = 'assist'\n", encoding="utf-8")
        _write_config(Console(quiet=True), tmp_path, 8080)
        text = cfg.read_text()
        assert "[plugin.vision]" in text
        assert "http://127.0.0.1:8080/v1" in text
        assert "[codegraph]" in text  # existing content preserved

    def test_never_clobbers_existing_plugin_vision(self, tmp_path):
        from cgh_vision.setup_llamacpp import _write_config
        from rich.console import Console

        cfg = tmp_path / ".codegraph" / "config.toml"
        cfg.parent.mkdir(parents=True)
        original = "[plugin.vision]\nollama_url = 'http://127.0.0.1:11434'\n"
        cfg.write_text(original, encoding="utf-8")
        _write_config(Console(quiet=True), tmp_path, 8080)
        assert cfg.read_text() == original  # untouched


class TestHint:
    def test_hint_appended_to_prompts(self, monkeypatch):
        seen = []

        def spy(model, path, prompt, config=None, timeout_s=120):
            seen.append(prompt)
            return '{"summary":"x","content":["logo"],"text_density":"none"}'

        monkeypatch.setattr(pipeline, "ask", spy)
        pipeline.inventory(IMG, {"hint": "labels are in French"})
        assert "Additional guidance" in seen[0]
        assert "labels are in French" in seen[0]

    def test_no_hint_leaves_prompt_untouched(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            pipeline,
            "ask",
            lambda m, p, prompt, config=None, timeout_s=120: (
                seen.append(prompt)
                or '{"summary":"x","content":["logo"],"text_density":"none"}'
            ),
        )
        pipeline.inventory(IMG, {})
        assert "Additional guidance" not in seen[0]

    def test_hint_keeps_the_json_contract_first(self, monkeypatch):
        from cgh_vision.pipeline import _with_hint

        out = _with_hint("RULES: return JSON", {"hint": "be terse"})
        assert out.startswith("RULES: return JSON")  # contract precedes the nudge
        assert "be terse" in out


def test_ask_ollama_404_raises_clear_visionerror(tmp_path, monkeypatch):
    """A 404 from Ollama /api/generate (model not pulled) must surface as a
    VisionError naming the model and how to get it, not a raw HTTPError."""
    import urllib.error
    import urllib.request

    from cgh_vision import backends

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    def _raise_404(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _raise_404)

    import pytest

    with pytest.raises(backends.VisionError) as ei:
        backends._ask_ollama("qwen2.5vl:7b", img, "prompt", {}, 5)
    msg = str(ei.value)
    assert "qwen2.5vl:7b" in msg and "ollama pull" in msg


def test_manual_gguf_steps_mapped_model():
    """A known default model fills in its real HF repo, quant, two-pass
    download (weights + mmproj) and the `ollama create` under the exact
    profile name."""
    from cgh_vision import backends

    steps = backends.manual_gguf_steps("qwen2.5vl:3b")
    assert "ggml-org/Qwen2.5-VL-3B-Instruct-GGUF" in steps
    assert '--include "*Q4_K_M*"' in steps and '--include "*mmproj*"' in steps
    assert "ollama create qwen2.5vl:3b" in steps
    assert "mmproj" in steps  # the projector is called out as mandatory


def test_manual_gguf_steps_unmapped_model_uses_placeholders():
    """An unmapped custom model still gets the full shape, with a repo
    placeholder and its own name in the `ollama create` line."""
    from cgh_vision import backends

    steps = backends.manual_gguf_steps("my-vlm:custom")
    assert "<org>/<Model>-GGUF" in steps
    assert "ollama create my-vlm:custom" in steps


def test_ask_ollama_404_prints_manual_steps_when_hf_fetch_fails(tmp_path, monkeypatch):
    """When the model is missing AND the auto HF pull cannot resolve it,
    the VisionError carries the by-hand GGUF registration steps, not just
    a pointer to the README."""
    import urllib.error
    import urllib.request

    import pytest
    from cgh_vision import backends

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    def _raise_404(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _raise_404)
    monkeypatch.setattr(backends, "fetch_model_from_hf", lambda m, c: False)

    with pytest.raises(backends.VisionError) as ei:
        backends._ask_ollama("qwen2.5vl:3b", img, "prompt", {}, 5)
    msg = str(ei.value)
    assert "hf download" in msg and "ollama create qwen2.5vl:3b" in msg


def test_fetch_model_from_hf_gated_and_mapped(monkeypatch):
    """No auto-fetch when disabled, unmapped, or ollama absent."""
    import shutil

    from cgh_vision import backends

    # opt-out wins
    assert (
        backends.fetch_model_from_hf("qwen2.5vl:3b", {"vision_auto_fetch": False})
        is False
    )
    # unmapped model
    assert backends.fetch_model_from_hf("some-custom:latest", {}) is False
    # mapped but no ollama binary
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert backends.fetch_model_from_hf("qwen2.5vl:3b", {}) is False


def test_fetch_model_from_hf_runs_ollama(monkeypatch):
    import shutil
    import subprocess

    from cgh_vision import backends

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/ollama")
    calls = []

    class _OK:
        returncode = 0

    def _run(cmd, **kw):
        calls.append(cmd)
        return _OK()

    monkeypatch.setattr(subprocess, "run", _run)
    assert backends.fetch_model_from_hf("qwen2.5vl:3b", {}) is True
    # pulled the HF spec, then aliased it to the profile name
    assert calls[0][:2] == ["ollama", "pull"]
    assert calls[0][2].startswith("hf.co/")
    assert calls[1][:2] == ["ollama", "cp"] and calls[1][3] == "qwen2.5vl:3b"


def test_ask_ollama_404_fetches_then_retries(tmp_path, monkeypatch):
    """On a 404 for a mapped model, fetch from HF and retry once."""
    import urllib.error
    import urllib.request

    from cgh_vision import backends

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)

    monkeypatch.setattr(backends, "fetch_model_from_hf", lambda m, c: True)
    state = {"n": 0}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"response": "ok"}'

    def _urlopen(req, timeout=0):
        state["n"] += 1
        if state["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    out = backends._ask_ollama("qwen2.5vl:3b", img, "p", {}, 5)
    assert out == "ok" and state["n"] == 2  # 404, fetched, retried, succeeded


def _png(tmp_path):
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    return p


def test_ask_ollama_timeout_names_the_slow_model_not_a_dead_daemon(
    tmp_path, monkeypatch
):
    """A request timeout means the daemon is up but slow (cold model / CPU);
    the error must say so, not falsely ask if the daemon is running."""
    import urllib.request

    import pytest
    from cgh_vision import backends

    def _timeout(req, timeout=0):
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", _timeout)
    with pytest.raises(backends.VisionError) as ei:
        backends._ask_ollama("qwen2.5vl:3b", _png(tmp_path), "p", {}, 300)
    msg = str(ei.value)
    assert "timed out" in msg and "timeout_s" in msg
    assert "is the daemon running" not in msg.lower()


def test_ask_ollama_connection_refused_asks_if_daemon_runs(tmp_path, monkeypatch):
    import urllib.error
    import urllib.request

    import pytest
    from cgh_vision import backends

    def _refused(req, timeout=0):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    monkeypatch.setattr(urllib.request, "urlopen", _refused)
    with pytest.raises(backends.VisionError) as ei:
        backends._ask_ollama("qwen2.5vl:3b", _png(tmp_path), "p", {}, 300)
    msg = str(ei.value)
    assert "daemon" in msg.lower() and "ollama serve" in msg
