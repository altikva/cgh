# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Deferred LLM scanner. Probes a file's text with the local
#              or configured LLM and emits pii.llm.<category> findings,
#              carrying only the match count, never the matched value, like
#              the regex and NER tiers. Deferred (never inline): an LLM
#              call per file is far too slow for the indexing hot path. A
#              denied egress gate or an unreachable backend yields no
#              findings, so a scan never fails because of this tier.

from __future__ import annotations

from pathlib import Path

from codegraph.plugin_api import ScanFinding

from . import llm

_MAX_CHARS = 100_000


class LlmPiiScanner:
    """Deferred scanner producing pii.llm.<category> counts."""

    name = "pii-llm"
    deferred = True

    def __init__(self, repo_root: str | Path, config: dict) -> None:
        self._repo_root = repo_root
        self._config = config

    def scan(self, path: Path, text: str, index) -> list[ScanFinding]:
        try:
            hits = llm.probe(
                text[:_MAX_CHARS], self._config, self._repo_root, str(path)
            )
        except llm.LlmProbeError:
            # Egress denied for this file (cloud endpoint, not allowed).
            # Already audited in the gate; no finding, no crash.
            return []
        counts: dict[str, list[int]] = {}
        for cat, quote in hits:
            idx = text.find(quote)
            line = text.count("\n", 0, idx) + 1 if idx != -1 else 1
            counts.setdefault(f"pii.llm.{cat}", []).append(line)
        return [
            ScanFinding(key=key, value=str(len(lines)), line=lines[0], severity="warn")
            for key, lines in counts.items()
        ]
