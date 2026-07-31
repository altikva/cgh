# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Deferred NER scanner (person names, locations) backed by
#              presidio. Heavy by nature, so it runs through the deferred
#              queue, never inline. Values carry only counts, like the
#              regex tier.

from __future__ import annotations

from pathlib import Path

# Import at module load so registration fails fast (and cleanly) when the
# ner extra is not installed; see the register() guard in __init__.
from presidio_analyzer import AnalyzerEngine

from codegraph.plugin_api import ScanFinding

_ENTITY_KEYS = {
    "PERSON": "pii.person",
    "LOCATION": "pii.location",
}
_MAX_CHARS = 100_000  # NER on multi-MB blobs is pointless and slow


class NerScanner:
    """Deferred scanner producing pii.person / pii.location counts."""

    name = "pii-ner"
    deferred = True

    def __init__(self) -> None:
        self._engine = AnalyzerEngine()

    def scan(self, path: Path, text: str, index) -> list[ScanFinding]:
        results = self._engine.analyze(
            text=text[:_MAX_CHARS],
            entities=list(_ENTITY_KEYS),
            language="en",
        )
        counts: dict[str, list[int]] = {}
        for r in results:
            key = _ENTITY_KEYS.get(r.entity_type)
            if key is None or r.score < 0.5:
                continue
            line = text.count("\n", 0, r.start) + 1
            counts.setdefault(key, []).append(line)
        return [
            ScanFinding(key=key, value=str(len(lines)), line=lines[0], severity="warn")
            for key, lines in counts.items()
        ]
