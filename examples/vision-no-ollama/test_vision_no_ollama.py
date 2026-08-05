# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The no-Ollama path exercised without any server: fake the
#              HTTP transport and assert cgh-vision selects the OpenAI
#              backend and posts a /chat/completions request with the
#              image, and that a loopback endpoint stays local.

from __future__ import annotations

import io
import json

import pytest

pytest.importorskip("cgh_vision")

import cgh_vision.backends as backends

CONFIG = {"openai_base_url": "http://127.0.0.1:8080/v1"}


def test_openai_backend_selected_and_local():
    assert backends.backend_kind(CONFIG) == "openai"
    assert backends.is_local(CONFIG) is True
    assert backends.is_local({"openai_base_url": "https://gw.corp/v1"}) is False


def test_posts_chat_completions_with_the_image(tmp_path, monkeypatch):
    img = tmp_path / "d.png"
    img.write_bytes(b"\x89PNG" + b"0" * 64)
    seen = {}

    def fake_urlopen(req, timeout=120):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode())
        return io.BytesIO(
            json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()
        )

    monkeypatch.setattr(backends.urllib.request, "urlopen", fake_urlopen)
    backends.ask("qwen2.5-vl", img, "read this", CONFIG)

    assert seen["url"].endswith("/chat/completions")
    content = seen["body"]["messages"][0]["content"]
    assert content[0]["text"] == "read this"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
