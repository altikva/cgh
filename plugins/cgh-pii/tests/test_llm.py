# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The optional LLM PII tier: reply parsing and vocabulary
#              enforcement, the loopback/cloud egress class, the gate that
#              refuses a cloud probe without pii_llm_allow_remote (and
#              audits both ways), the redact path fed with LLM hits, and
#              the deferred scanner emitting count-only findings.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_pii")

from cgh_pii import llm
from cgh_pii.redact import redact


def test_parse_tolerates_fence_and_drops_off_vocabulary():
    reply = (
        "here you go:\n```json\n"
        '[{"category": "person", "quote": "Jia Tan"},'
        ' {"category": "alien", "quote": "x"},'
        ' {"category": "id_number", "quote": ""}]\n```'
    )
    hits = llm._parse(reply)
    assert hits == [("person", "Jia Tan")]  # bad category and empty quote dropped


def test_parse_bad_json_is_empty():
    assert llm._parse("not json") == []
    assert llm._parse("") == []


def test_egress_class_loopback_vs_cloud():
    assert llm.egress_class({"llm_ollama_url": "http://127.0.0.1:11434"}) == "local"
    assert llm.egress_class({"llm_ollama_url": "http://10.0.0.5:11434"}) == "cloud"
    assert (
        llm.egress_class({"llm_openai_base_url": "https://api.acme.com/v1"}) == "cloud"
    )


def test_cloud_probe_denied_without_allow_remote_and_audited(monkeypatch, tmp_path):
    """A non-loopback endpoint must refuse to send file content unless
    pii_llm_allow_remote is set, and the refusal is written to the log."""
    logged: list[tuple] = []
    monkeypatch.setattr(
        "codegraph.plugin_api.activity_log",
        lambda root, event, detail: logged.append((event, detail)),
        raising=False,
    )
    cfg = {"llm_openai_base_url": "https://api.acme.com/v1", "llm_openai_model": "m"}
    with pytest.raises(llm.LlmProbeError):
        llm.probe("Jane at 12 Baker St", cfg, tmp_path, "secret.txt")
    assert any(e == "pii_llm_refused" for e, _ in logged)


def test_cloud_probe_allowed_with_flag_is_audited(monkeypatch, tmp_path):
    logged: list[tuple] = []
    monkeypatch.setattr(
        "codegraph.plugin_api.activity_log",
        lambda root, event, detail: logged.append((event, detail)),
        raising=False,
    )
    monkeypatch.setattr(
        llm, "_call", lambda text, cfg: '[{"category":"other","quote":"Z"}]'
    )
    cfg = {
        "llm_openai_base_url": "https://api.acme.com/v1",
        "llm_openai_model": "m",
        "pii_llm_allow_remote": True,
    }
    hits = llm.probe("Z is a token", cfg, tmp_path, "f.txt")
    assert hits == [("other", "Z")]
    assert any(e == "pii_llm_probe" for e, _ in logged)


def test_local_probe_needs_no_flag(monkeypatch, tmp_path):
    monkeypatch.setattr("codegraph.state.activity.log", lambda *a, **k: None)
    monkeypatch.setattr(
        llm, "_call", lambda text, cfg: '[{"category":"person","quote":"Ada"}]'
    )
    hits = llm.probe(
        "Ada wrote this", {"llm_ollama_url": "http://127.0.0.1:11434"}, tmp_path
    )
    assert hits == [("person", "Ada")]


def test_redact_with_llm_hits_redacts_and_propagates():
    """An LLM quote (mapped to a redaction category) is redacted at every
    occurrence, like a NER value; an invented quote redacts nothing."""
    text = "Contract with Umbrella Corp. Umbrella Corp signed twice."
    hits = [("other", "Umbrella Corp"), ("other", "Ghost Ltd")]  # Ghost absent
    out, counts = redact(text, only=["other"], llm_hits=hits)
    assert "Umbrella Corp" not in out
    assert counts.get("other") == 2  # both occurrences
    assert "[OTHER_1]" in out


def test_scanner_emits_count_only_findings(monkeypatch, tmp_path):
    from cgh_pii.llm_scanner import LlmPiiScanner

    monkeypatch.setattr(
        llm, "probe", lambda text, cfg, root, path: [("person", "Ada"), ("org", "ACME")]
    )
    sc = LlmPiiScanner(tmp_path, {"llm_ollama_url": "http://127.0.0.1:11434"})
    findings = sc.scan(tmp_path / "f.py", "Ada at ACME", None)
    keys = {f.key: f.value for f in findings}
    assert keys == {"pii.llm.person": "1", "pii.llm.org": "1"}


def test_scanner_returns_empty_when_egress_denied(monkeypatch, tmp_path):
    def _deny(text, cfg, root, path):
        raise llm.LlmProbeError("denied")

    from cgh_pii.llm_scanner import LlmPiiScanner

    monkeypatch.setattr(llm, "probe", _deny)
    sc = LlmPiiScanner(tmp_path, {})
    assert sc.scan(tmp_path / "f.py", "text", None) == []


def test_resolve_ollama_model(monkeypatch):
    from cgh_pii import llm

    u = "http://127.0.0.1:11434"
    llm._tags_cache[u] = (
        9e18,
        frozenset({"gemma3:4b", "qwen2.5vl:3b", "nomic-embed:latest"}),
    )
    # configured-and-installed wins
    assert llm.resolve_ollama_model(u, "gemma3:4b") == "gemma3:4b"
    # configured-but-absent -> auto-pick, qwen family preferred, embed excluded
    assert llm.resolve_ollama_model(u, "qwen2.5:3b") == "qwen2.5vl:3b"
    # nothing installed -> None (caller degrades)
    llm._tags_cache[u] = (9e18, frozenset())
    assert llm.resolve_ollama_model(u, "gemma3:4b") is None
