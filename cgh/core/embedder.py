# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Text embedders for semantic search. The default is a pure-numpy
#              lexical hashing embedder: deterministic, offline, no model to
#              download, so it ships in the standalone binary. The neural
#              embedder (fastembed / bge-small) is opt-in via the `neural`
#              extra; a measured benchmark showed it is the one that lifts
#              recall on paraphrased "by concept" queries, where the lexical
#              embedder only duplicates BM25's signal. Mode is chosen once; a
#              query must be embedded with the same mode the corpus was.

from __future__ import annotations

import hashlib
import re
from typing import Protocol

import numpy as np

_DEFAULT_DIM = 512
_TOKEN = re.compile(r"[A-Za-z0-9]+")


class Embedder(Protocol):
    """Maps texts to unit-norm vectors of a fixed dimension."""

    dim: int
    mode: str

    def embed(self, texts: list[str]) -> np.ndarray:  # (len(texts), dim)
        ...


def _split_identifier(word: str) -> list[str]:
    """Break camelCase and snake_case into lowercase word parts."""
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", word).replace("_", " ").split()
    return [p.lower() for p in parts]


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for w in _TOKEN.findall(text):
        out += _split_identifier(w)
    return out


def _char_ngrams(text: str, n: int = 3) -> list[str]:
    s = "^" + re.sub(r"\s+", " ", text.lower()) + "$"
    return [s[i : i + n] for i in range(max(0, len(s) - n + 1))]


class LexicalEmbedder:
    """Signed feature hashing over char 3-grams and word tokens.

    Deterministic and offline: the same text always maps to the same vector,
    so the corpus and a later query embed identically. It captures subword and
    morphological overlap, not true synonymy, which is why it is a baseline and
    fallback rather than the semantic win (that is the neural embedder)."""

    mode = "lexical"

    def __init__(self, dim: int = _DEFAULT_DIM) -> None:
        self.dim = dim

    def _one(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for feat in _char_ngrams(text) + tokenize(text):
            h = int.from_bytes(
                hashlib.blake2b(feat.encode(), digest_size=8).digest(), "big"
            )
            v[h % self.dim] += 1.0 if (h >> 20) & 1 else -1.0
        n = float(np.linalg.norm(v))
        return v / n if n else v

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.vstack([self._one(t) for t in texts])


class NeuralEmbedder:
    """fastembed (bge-small-en-v1.5) ONNX embeddings, loaded lazily.

    Opt-in: the model is ~90 MB downloaded on first use, so it is never in the
    standalone binary. Requires the `neural` extra (`pip install cgh[neural]`)."""

    mode = "neural"
    _MODEL = "BAAI/bge-small-en-v1.5"

    def __init__(self) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover - exercised via get_embedder
            raise EmbedderUnavailable(
                "neural embeddings need the optional dependency: "
                "pip install 'cgh[neural]'"
            ) from exc
        self._emb = TextEmbedding(self._MODEL)
        self.dim = 384  # bge-small-en-v1.5

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        mat = np.vstack(list(self._emb.embed(texts))).astype(np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        return mat / np.where(norms == 0, 1.0, norms)


class EmbedderUnavailable(RuntimeError):
    """The requested embedder cannot be constructed (missing optional dep)."""


def get_embedder(mode: str = "lexical") -> Embedder:
    """Build the embedder for a mode. 'lexical' (default) is always available;
    'neural' needs the optional dependency and raises EmbedderUnavailable if
    it is missing."""
    if mode == "neural":
        return NeuralEmbedder()
    if mode == "lexical":
        return LexicalEmbedder()
    raise ValueError(f"unknown embed mode: {mode!r} (use 'lexical' or 'neural')")
