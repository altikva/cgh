# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Inline classify scanner. Human labels win: they become the
#              `confidential` finding (true = block, false = clears the
#              strict allowlist). Model predictions only ever block: a
#              predicted-confidential file gets `confidential = true`, a
#              predicted-public file gets `confidential.predicted`, which
#              the gate ignores, because only a human may clear a file in
#              secure mode. Uncertain predictions get
#              `confidential.uncertain` so `cgh classify review` can list
#              them for a human pass.

from __future__ import annotations

from pathlib import Path

from codegraph.plugin_api import ScanFinding

from .labels import load_labels
from .model import NaiveBayesModel, model_path


class ClassifyScanner:
    """Inline scanner writing confidentiality findings."""

    name = "classify"
    deferred = False

    def __init__(self, config: dict, repo_root) -> None:
        self.config = config
        self.repo_root = repo_root
        self._model: NaiveBayesModel | None = None
        self._model_loaded = False

    def _get_model(self) -> NaiveBayesModel | None:
        if not self._model_loaded:
            self._model = NaiveBayesModel.load(model_path(self.repo_root))
            self._model_loaded = True
        return self._model

    def scan(self, path: Path, text: str, index) -> list[ScanFinding]:
        labels = load_labels(self.repo_root)
        label = labels.get(str(Path(path).resolve()))
        if label is True:
            return [ScanFinding(key="confidential", value="true", severity="block")]
        if label is False:
            return [ScanFinding(key="confidential", value="false", severity="info")]

        model = self._get_model()
        if model is None:
            return []

        p = model.predict(text)
        threshold = float(self.config.get("threshold", 0.7))
        low = float(self.config.get("uncertain_low", 0.35))
        high = float(self.config.get("uncertain_high", 0.65))

        if p >= threshold:
            return [
                ScanFinding(key="confidential", value="true", severity="block"),
                ScanFinding(key="confidential.p", value=f"{p:.2f}"),
            ]
        if low <= p <= high:
            return [ScanFinding(key="confidential.uncertain", value=f"{p:.2f}")]
        return [ScanFinding(key="confidential.predicted", value="false")]
