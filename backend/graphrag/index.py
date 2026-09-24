"""Vector index behind one interface.

`LocalVectorIndex` is a numpy matrix on disk. `TigerGraphVectorIndex` is the
same interface over the graph's vector store, for when the graph lane ships.
The retriever only ever sees `VectorIndex`, so switching is a config change.
"""

from __future__ import annotations

import json
import pickle
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from backend.config import get_settings
from backend.graphrag.chunker import Chunk, build_corpus
from backend.graphrag.embeddings import Embedder, cosine_top_k, get_embedder

_LOCK = threading.Lock()


@dataclass
class Hit:
    ref: str
    text: str
    kind: str
    score: float
    metadata: dict[str, Any]


class VectorIndex(ABC):
    @abstractmethod
    def search(self, query: str, k: int, kinds: tuple[str, ...] | None = None) -> list[Hit]: ...

    @abstractmethod
    def by_ref(self, ref: str) -> Hit | None: ...


class LocalVectorIndex(VectorIndex):
    def __init__(self, chunks: list[Chunk], vectors: np.ndarray, embedder: Embedder) -> None:
        self.chunks = chunks
        self.vectors = vectors
        self.embedder = embedder
        self._by_ref = {c.ref: i for i, c in enumerate(chunks)}

    def search(self, query: str, k: int, kinds: tuple[str, ...] | None = None) -> list[Hit]:
        q = self.embedder.encode_one(query)
        if kinds:
            keep = [i for i, c in enumerate(self.chunks) if c.kind in kinds]
            if not keep:
                return []
            sub = self.vectors[keep]
            pairs = cosine_top_k(q, sub, k)
            return [self._hit(keep[i], s) for i, s in pairs]
        return [self._hit(i, s) for i, s in cosine_top_k(q, self.vectors, k)]

    def by_ref(self, ref: str) -> Hit | None:
        i = self._by_ref.get(ref)
        return self._hit(i, 1.0) if i is not None else None

    def _hit(self, i: int, score: float) -> Hit:
        c = self.chunks[i]
        return Hit(ref=c.ref, text=c.text, kind=c.kind, score=round(score, 4), metadata=c.metadata)

    # ---- persistence -----------------------------------------------------

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / "vectors.npy", self.vectors)
        with (directory / "chunks.pkl").open("wb") as fh:
            pickle.dump(self.chunks, fh)
        (directory / "meta.json").write_text(
            json.dumps({"embedder": self.embedder.name, "dim": int(self.vectors.shape[1]), "n": len(self.chunks)}),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, directory: Path, embedder: Embedder) -> "LocalVectorIndex | None":
        meta_path = directory / "meta.json"
        if not meta_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        # A cache built by a different embedder would silently return nonsense.
        if meta.get("embedder") != embedder.name:
            return None
        vectors = np.load(directory / "vectors.npy")
        with (directory / "chunks.pkl").open("rb") as fh:
            chunks = pickle.load(fh)
        return cls(chunks, vectors, embedder)


class TigerGraphVectorIndex(VectorIndex):
    """Same interface over TigerGraph's vector store.

    Left unimplemented on purpose: the graph lane owns the query names, and
    guessing them here would produce a file that looks finished and is not.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def search(self, query: str, k: int, kinds: tuple[str, ...] | None = None) -> list[Hit]:
        raise NotImplementedError("TigerGraph vector search lands with the graph lane")

    def by_ref(self, ref: str) -> Hit | None:
        raise NotImplementedError("TigerGraph vector search lands with the graph lane")


@lru_cache(maxsize=1)
def get_index() -> VectorIndex:
    settings = get_settings()
    directory = settings.cache_dir / "graphrag_index"
    embedder = get_embedder()
    with _LOCK:
        cached = LocalVectorIndex.load(directory, embedder)
        if cached is not None:
            return cached
        chunks = build_corpus()
        vectors = embedder.encode([c.text for c in chunks])
        index = LocalVectorIndex(chunks, vectors, embedder)
        index.save(directory)
        return index
