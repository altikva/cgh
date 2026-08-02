# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-02
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The example's test pattern for your own suite: fake the
#              model transport (cgh_vision.pipeline.ask) so no Ollama
#              daemon is needed, then assert on routing and on the
#              extraction contract.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_vision")

from codegraph import sdk


class ScriptedTransport:
    """Returns canned model replies in order."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)

    def __call__(self, model, path, prompt, config=None, timeout_s=120):
        return self.replies.pop(0) if self.replies else "{}"


def _fake(monkeypatch, replies: list[str]) -> None:
    import cgh_vision.pipeline as pipeline

    monkeypatch.setattr(pipeline, "ask", ScriptedTransport(replies))


def test_logo_is_not_extracted(monkeypatch, tmp_path):
    _fake(
        monkeypatch,
        ['{"summary": "a logo", "content": ["logo"], "text_density": "none"}'],
    )
    inv = sdk.image_inventory(tmp_path / "logo.png")
    assert inv["content"] == ["logo"]  # router: nothing else to run


def test_diagram_contract(monkeypatch, tmp_path):
    _fake(
        monkeypatch,
        [
            '{"nodes": ["API", "DB 10.0.0.5"], "edges": [["API", "DB 10.0.0.5"]],'
            ' "zones": []}',
            '{"title": "T", "kinds": {"API": "service"}, "tech": {}}',
            '{"edges": []}',
        ],
    )
    ex = sdk.extract_diagram(tmp_path / "archi.png")
    labels = [n["label"] for n in ex["nodes"]]
    assert labels == ["API", "DB"]  # identity split off the label
    assert ex["nodes"][1]["identities"] == ["10.0.0.5"]
    assert len(ex["edges"]) == 1
    assert "```" not in ex["mermaid"] and "flowchart" in ex["mermaid"]


def test_missing_daemon_raises_a_named_error(monkeypatch, tmp_path):
    from cgh_vision.backends import VisionError

    monkeypatch.setattr("cgh_vision.backends.available", lambda cfg: False)

    def refuse(*a, **k):
        raise VisionError("Ollama daemon not reachable")

    monkeypatch.setattr("cgh_vision.pipeline.ask", refuse)
    with pytest.raises(VisionError):
        sdk.image_inventory(tmp_path / "x.png")
