"""Embeddings, local and free.

Default is sentence-transformers with BAAI/bge-small-en-v1.5, which needs no API
key. The hashing backend is a deterministic fallback so the pipeline still runs
where the model cannot be downloaded; it is weaker, and the index records which
one produced its vectors so a mixed index cannot be queried by accident.
"""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from functools import lru_cache

import numpy as np

from backend.config import get_settings

_TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(ABC):
    name: str = "base"
    dim: int = 0

    @abstractmethod
    def encode(self, texts: list[str]) -> np.ndarray: ...

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


class HashingEmbedder(Embedder):
    """Hashed word and bigram counts, L2 normalised.

    Not a semantic model. It matches on shared vocabulary, which is enough for
    this corpus because the closed-case notes are template text where the
    discriminating words (the pattern sentence, the device string) are literal.
    """

    name = "hashing"

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def _bucket(self, token: str) -> int:
        return int(hashlib.blake2b(token.encode(), digest_size=8).hexdigest(), 16) % self.dim

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            tokens = _TOKEN.findall(text.lower())
            grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
            for token in grams:
                out[row, self._bucket(token)] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.maximum(norms, 1e-9)


class SentenceTransformerEmbedder(Embedder):
    name = "sentence-transformers"

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self.name = f"sentence-transformers:{model_name}"
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(
            self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False),
            dtype=np.float32,
        )


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    settings = get_settings()
    if settings.embedding_provider == "local":
        try:
            return SentenceTransformerEmbedder(settings.embedding_model)
        except Exception:
            # No network or no model on disk. Say nothing clever, just degrade.
            return HashingEmbedder()
    return HashingEmbedder()


def cosine_top_k(query: np.ndarray, matrix: np.ndarray, k: int) -> list[tuple[int, float]]:
    if matrix.size == 0:
        return []
    scores = matrix @ query
    k = min(k, len(scores))
    idx = np.argpartition(-scores, k - 1)[:k]
    idx = idx[np.argsort(-scores[idx])]
    return [(int(i), float(scores[i])) for i in idx]
