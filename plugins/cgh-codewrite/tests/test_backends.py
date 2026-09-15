# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Backend selection and the local OllamaBackend: resolve_backend
#              picks CLI vs Ollama from config, and OllamaBackend parses an
#              Ollama reply and swallows a dead endpoint into an empty result.

from __future__ import annotations

import json
import urllib.error

import pytest

pytest.importorskip("cgh_codewrite")

from cgh_codewrite.backends import CliBackend, OllamaBackend, resolve_backend


def test_resolve_cli_from_command():
    b = resolve_backend({"command": "claude -p"})
    assert isinstance(b, CliBackend)
    assert b.is_local is False and b.name == "cli:claude"


def test_resolve_ollama_by_kind():
    b = resolve_backend({"backend": "ollama", "ollama_model": "gemma3:4b"})
    assert isinstance(b, OllamaBackend)
    assert b.is_local is True and b.name == "ollama:gemma3:4b"


def test_resolve_ollama_by_model_without_command():
    assert isinstance(resolve_backend({"ollama_model": "llama3"}), OllamaBackend)


def test_command_wins_when_both_present_and_no_kind():
    # Both configured, no explicit backend kind: the command path is taken.
    assert isinstance(
        resolve_backend({"command": "claude -p", "ollama_model": "m"}), CliBackend
    )


def test_ollama_kind_overrides_command():
    b = resolve_backend({"backend": "ollama", "ollama_model": "m", "command": "claude"})
    assert isinstance(b, OllamaBackend)


def test_resolve_none_when_empty():
    assert resolve_backend({}) is None


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def test_ollama_generate_parses_response(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=0: _FakeResp({"response": "print(1)\n"}),
    )
    text, cost = OllamaBackend("m").generate("sys", "usr")
    assert text == "print(1)\n" and cost == 0.0


def test_ollama_generate_swallows_a_dead_endpoint(monkeypatch):
    def boom(req, timeout=0):
        raise urllib.error.URLError("no ollama running")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert OllamaBackend("m").generate("s", "u") == ("", 0.0)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
