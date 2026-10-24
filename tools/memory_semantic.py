"""Local semantic recall leg (DL-P2.10): fastembed + BAAI/bge-small-zh-v1.5.

A fill-in leg on top of the #584 hybrid ranking, in exactly the mode the
DL-P2.10 investigation measured ("mode C"): embeddings contribute ONLY
when the lexical gate is completely starved, filling the one recall slot
the BM25 fill already owns; a strong lexical hit can never be pushed out.
Measured on the DL-P2.6 benchmark: top-2 0.92->0.94, paraphrase 0.85->0.95,
irrelevant 0.05->0.04 (D:\\wt\\logs\\DL-P2.10-INV.md).

Process-level singleton mirroring ``tools/memory_hybrid.HybridTopicIndex``:
fastembed is imported lazily on the first starved recall with the flag ON;
model files are cached under ``<home>/cache/fastembed``; topic vectors are
cached persistently keyed by content hash (``vectors.npz``) so a store
rewrite or process restart re-embeds only new entries. Every failure mode
(not installed, offline, download error, ONNX error) logs once at warning
and returns an empty rank list — the caller keeps the hybrid BM25 fill
unchanged; nothing ever raises into the turn.

The first starved recall never blocks the reply on a model download: when
the model files are absent, a background thread downloads/loads them and
that one recall degrades to the hybrid result (≤2 s budget).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
# bge-small-zh-v1.5 is Chinese-first. Per the BAAI model card the v1.5
# models relaxed the "为这个句子生成表示…" query-instruction prefix
# ("not so necessary"; fastembed's model catalog carries the same note),
# so queries and passages both embed bare — query_embed()/embed().
# No absolute similarity floor on the fill slot: the investigation measured
# mode C without one and flagged an absolute threshold as a new tuning
# surface (INV risk 5) — the lexical-starvation gate is the precision
# control, not a cosine cutoff.
SEMANTIC_FETCH = 8  # rows pulled from the rank list before the caller caps

_VEC_CACHE_NAME = "vectors.npz"

_UNATTEMPTED = 0
_LOADING = 1
_READY = 2
_FAILED = 3

_STATE_LOCK = threading.Lock()


def _model_cache_dir() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "cache" / "fastembed"


def _doc_text(topic: dict[str, Any]) -> str:
    """Same fields the FTS index sees (``memory_hybrid._INDEXED_FIELDS``)."""
    title = str(topic.get("title") or "").strip()
    keywords = " ".join(str(k) for k in (topic.get("keywords") or []) if str(k).strip())
    summary = str(topic.get("summary") or "").strip()
    return f"{title} {keywords} {summary}".strip()


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SemanticTopicIndex:
    """Embedding index over the topic store; rebuilt on store change.

    The store is bounded (<=64 topics by design), so vectors live in memory
    as a normalized matrix and search is a brute-force dot product — no
    vector DB (INV Q1: numpy brute force is 0.1 ms-scale at this size).
    Rebuilds are guarded by the store signature (mtime_ns, size, row_count)
    exactly like ``HybridTopicIndex``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model: Any = None
        self._model_state = _UNATTEMPTED
        self._warned: set[str] = set()
        self._signature: tuple[int, int, int] | None = None
        self._keys: list[str] = []
        self._matrix: Any = None  # normalized ndarray once built
        self._vec_cache: dict[str, Any] = {}  # sha256(text) -> ndarray
        self.available = False

    # ── warnings (once per failure class, never per turn) ───────────────

    def _warn_once(self, key: str, msg: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        logger.warning("%s", msg)

    # ── model lifecycle ─────────────────────────────────────────────────

    def _load_model(self) -> Any:
        """Load fastembed in-process. Caller holds no lock.

        The fastembed import itself (0.7 s: onnxruntime, requests, the
        image-model chain) runs in this background thread too, so a starved
        recall never pays it on the reply path.
        """
        from fastembed import TextEmbedding

        cache_dir = _model_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        model = TextEmbedding(model_name=MODEL_NAME, cache_dir=str(cache_dir))
        logger.info("semantic recall model ready (%s, cache %s)", MODEL_NAME, cache_dir)
        return model

    def _start_model_thread(self) -> None:
        """Import + load on a daemon thread; the recall never blocks on it."""

        def _worker() -> None:
            try:
                model = self._load_model()
            except Exception:
                with _STATE_LOCK:
                    self._model_state = _FAILED
                self._warn_once(
                    "model-download",
                    "semantic recall disabled: model load/download failed "
                    f"({MODEL_NAME}); keeping hybrid BM25 fill",
                )
                logger.debug("fastembed load failure detail", exc_info=True)
                return
            with _STATE_LOCK:
                self._model = model
                self._model_state = _READY

        logger.info(
            "semantic recall model loading %s in background (hybrid BM25 fill serves until ready)",
            MODEL_NAME,
        )
        threading.Thread(target=_worker, name="memory-semantic-model", daemon=True).start()

    def _get_model(self) -> Any:
        """Return the loaded model, or None; never raises into the turn.

        Both the cold download and the warm re-load (fastembed import +
        ONNX session, ~0.9 s) run on a daemon thread: a starved recall
        never blocks the reply beyond the flag check. Until the model is
        ready the recall degrades to the hybrid BM25 fill.
        """
        if self._model_state == _READY:
            return self._model
        if self._model_state == _UNATTEMPTED:
            with _STATE_LOCK:
                if self._model_state == _UNATTEMPTED:
                    self._model_state = _LOADING
                    self._start_model_thread()
        return None

    # ── persistent vector cache (content-hash keyed) ────────────────────

    def _vec_cache_path(self) -> Path:
        return _model_cache_dir() / _VEC_CACHE_NAME

    def _load_vec_cache(self) -> dict[str, Any]:
        try:
            import numpy as np

            path = self._vec_cache_path()
            if not path.exists():
                return {}
            with np.load(path, allow_pickle=False) as data:
                hashes = [str(h) for h in data["hashes"]]
                vectors = data["vectors"]
            return dict(zip(hashes, vectors))
        except Exception:
            # A corrupt/unreadable cache only costs re-embedding; never
            # breaks recall.
            self._warn_once("vec-cache-load", "semantic recall vector cache unreadable; re-embedding")
            logger.debug("vector cache load failure detail", exc_info=True)
            return {}

    def _save_vec_cache(self) -> None:
        try:
            import numpy as np

            path = self._vec_cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            # savez keeps a name already ending in .npz as-is.
            tmp = path.with_name(path.name + ".tmp.npz")
            np.savez(
                tmp,
                hashes=list(self._vec_cache.keys()),
                vectors=np.stack(list(self._vec_cache.values())),
            )
            os.replace(tmp, path)
        except Exception:
            self._warn_once("vec-cache-save", "semantic recall vector cache could not be saved")
            logger.debug("vector cache save failure detail", exc_info=True)

    # ── index build / search ────────────────────────────────────────────

    def ensure(self, rows: list[dict[str, Any]], signature: tuple[int, int, int]) -> bool:
        """Rebuild when ``signature`` changed; True when the index is usable."""
        model = self._get_model()
        if model is None:
            return False
        with self._lock:
            if self._matrix is not None and signature == self._signature:
                return True
            if not rows:
                # Same contract as HybridTopicIndex.ensure: nothing to
                # index is not an error, simply unusable.
                self._signature = signature
                self._keys = []
                self._matrix = None
                return False
            try:
                import numpy as np

                if not self._vec_cache:
                    self._vec_cache = self._load_vec_cache()
                keys = [str(row.get("key") or "") for row in rows]
                texts = [_doc_text(row) for row in rows]
                missing = [text for text in texts if _text_hash(text) not in self._vec_cache]
                if missing:
                    # Batch-embed every new entry in one pass (INV: 30 docs
                    # = 0.14 s); per-item failures abandon the leg.
                    fresh = list(model.embed(missing, batch_size=32))
                    if len(fresh) != len(missing):
                        raise RuntimeError(f"embedder returned {len(fresh)} of {len(missing)}")
                    for text, vector in zip(missing, fresh):
                        self._vec_cache[_text_hash(text)] = np.asarray(vector, dtype=np.float32)
                    self._save_vec_cache()
                matrix = np.stack([self._vec_cache[_text_hash(text)] for text in texts]).astype(np.float32)
                norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                matrix = matrix / norms
                self._keys = keys
                self._matrix = matrix
                self._signature = signature
                return True
            except Exception:
                self._warn_once("index-build", "semantic recall disabled: index build failed; keeping hybrid BM25 fill")
                logger.debug("semantic index build failure detail", exc_info=True)
                return False

    def search(self, query: str, limit: int = SEMANTIC_FETCH) -> list[tuple[str, float]]:
        """Return ``[(key, cosine)]`` best-first; empty on any failure."""
        model = self._get_model()
        if model is None:
            return []
        try:
            import numpy as np

            qvec = np.asarray(next(iter(model.query_embed(query))), dtype=np.float32)
            norm = float(np.linalg.norm(qvec))
            if norm <= 0.0:
                return []
            qvec = qvec / norm
            with self._lock:
                if self._matrix is None or not self._keys:
                    return []
                sims = (self._matrix @ qvec).tolist()
            ranked = sorted(zip(self._keys, sims), key=lambda item: (-item[1], item[0]))[:limit]
            return [(str(key), float(score)) for key, score in ranked]
        except Exception:
            self._warn_once("search", "semantic recall degraded: query embedding failed")
            logger.debug("semantic search failure detail", exc_info=True)
            return []

    def close(self) -> None:
        with self._lock:
            self._keys = []
            self._matrix = None
            self._signature = None


_GLOBAL_INDEX = SemanticTopicIndex()


def global_index() -> SemanticTopicIndex:
    return _GLOBAL_INDEX


def reset_global_index() -> None:
    """Test seam: replace the singleton (model, vectors, warnings) entirely."""
    global _GLOBAL_INDEX
    with _STATE_LOCK:
        _GLOBAL_INDEX = SemanticTopicIndex()


def semantic_rank(
    query: str,
    topics: list[dict[str, Any]],
    top_k: int = SEMANTIC_FETCH,
    signature: tuple[int, int, int] | None = None,
) -> list[tuple[str, float]]:
    """Rank ``topics`` against ``query``; empty list on any failure.

    ``signature`` reuses the caller's store signature when available (the
    hybrid path already stat'ed the store); otherwise it is derived here
    the same way ``memory_hybrid.store_signature`` does.
    """
    clean = str(query or "").strip()
    if not clean or not topics:
        return []
    if signature is None:
        try:
            from tools.topic_memory_store import topic_memory_path

            stat = topic_memory_path().stat()
            signature = (stat.st_mtime_ns, stat.st_size, len(topics))
        except OSError:
            return []
    index = global_index()
    if not index.ensure(topics, signature):
        return []
    return index.search(clean, limit=top_k)
