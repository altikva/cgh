# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The classifier: TF-IDF-weighted multinomial naive Bayes,
#              pure standard library. Small on purpose: the labels are
#              the asset, the model just generalizes them, and retraining
#              must stay instant so the label -> train -> review loop is
#              frictionless. Persisted as JSON next to the index.

from __future__ import annotations

import json
import math
import re
from pathlib import Path

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{2,30}")
_MAX_TOKENS_PER_DOC = 5000


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)][:_MAX_TOKENS_PER_DOC]


class NaiveBayesModel:
    """Multinomial NB over TF-IDF-ish weights (log-scaled counts,
    idf-weighted at train time). predict() returns P(confidential)."""

    def __init__(self) -> None:
        self.priors: dict[str, float] = {}
        self.token_logprob: dict[str, dict[str, float]] = {}
        self.vocab: set[str] = set()
        self.trained_on = 0

    # -- training ----------------------------------------------------------

    @classmethod
    def train(cls, docs: list[tuple[str, bool]]) -> NaiveBayesModel:
        """docs: [(text, is_confidential), ...] with both classes present."""
        model = cls()
        counts: dict[str, dict[str, float]] = {"yes": {}, "no": {}}
        doc_freq: dict[str, int] = {}
        n_docs = {"yes": 0, "no": 0}

        tokenized: list[tuple[list[str], str]] = []
        for text, confidential in docs:
            tokens = tokenize(text)
            label = "yes" if confidential else "no"
            n_docs[label] += 1
            tokenized.append((tokens, label))
            for token in set(tokens):
                doc_freq[token] = doc_freq.get(token, 0) + 1

        total = n_docs["yes"] + n_docs["no"]
        if not n_docs["yes"] or not n_docs["no"]:
            raise ValueError("need labeled examples of BOTH classes to train")

        idf = {
            token: math.log((1 + total) / (1 + df)) + 1.0
            for token, df in doc_freq.items()
        }
        for tokens, label in tokenized:
            tf: dict[str, int] = {}
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
            for token, count in tf.items():
                weight = (1 + math.log(count)) * idf[token]
                counts[label][token] = counts[label].get(token, 0.0) + weight

        model.vocab = set(doc_freq)
        vocab_size = max(len(model.vocab), 1)
        for label in ("yes", "no"):
            model.priors[label] = math.log(n_docs[label] / total)
            label_total = sum(counts[label].values())
            denom = label_total + vocab_size  # Laplace smoothing
            model.token_logprob[label] = {
                token: math.log((counts[label].get(token, 0.0) + 1.0) / denom)
                for token in model.vocab
            }
            model.token_logprob[label]["__unk__"] = math.log(1.0 / denom)
        model.trained_on = total
        return model

    # -- inference ---------------------------------------------------------

    def predict(self, text: str) -> float:
        """P(confidential) in [0, 1]."""
        scores = {}
        for label in ("yes", "no"):
            logp = self.priors[label]
            table = self.token_logprob[label]
            unk = table["__unk__"]
            for token in tokenize(text):
                logp += table.get(token, unk)
            scores[label] = logp
        # Softmax over the two log scores, guarded against overflow.
        m = max(scores.values())
        exp_yes = math.exp(scores["yes"] - m)
        exp_no = math.exp(scores["no"] - m)
        return exp_yes / (exp_yes + exp_no)

    # -- persistence -------------------------------------------------------

    def save(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "priors": self.priors,
                    "token_logprob": self.token_logprob,
                    "trained_on": self.trained_on,
                }
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> NaiveBayesModel | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            model = cls()
            model.priors = data["priors"]
            model.token_logprob = data["token_logprob"]
            model.vocab = set(data["token_logprob"]["yes"]) - {"__unk__"}
            model.trained_on = int(data.get("trained_on", 0))
            return model
        except (ValueError, KeyError, TypeError):
            return None


def model_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / ".codegraph" / "classify_model.json"
