"""Local semantic recall leg (DL-P2.10): local ONNX + BAAI/bge-small-zh-v1.5.

A fill-in leg on top of the #584 hybrid ranking, in exactly the mode the
DL-P2.10 investigation measured ("mode C"): embeddings contribute ONLY
when the lexical gate is completely starved, filling the one recall slot
the BM25 fill already owns; a strong lexical hit can never be pushed out.
Measured on the DL-P2.6 benchmark: top-2 0.92->0.94, paraphrase 0.85->0.95,
irrelevant 0.05->0.04 (D:\\wt\\logs\\DL-P2.10-INV.md).

Process-level singleton mirroring ``tools/memory_hybrid.HybridTopicIndex``:
the thin ONNX embedder is imported by the background worker with the flag ON;
model files are cached under ``<home>/cache/fastembed``; topic vectors are
cached persistently keyed by content hash (``vectors.npz``) so a store
rewrite or process restart re-embeds only new entries. Every failure mode
(not installed, offline, download error, ONNX error) logs once at warning
and returns an empty rank list — the caller keeps the hybrid BM25 fill
unchanged; nothing ever raises into the turn.

The first starved recall never blocks the reply on a model download: when
the model files are absent, a background thread downloads/loads them and
that recall degrades immediately to the hybrid result. Index refresh and
vector-cache IO also run off-turn; readers use complete immutable snapshots.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Any, NamedTuple

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


class _IndexSnapshot(NamedTuple):
    keys: tuple[str, ...]
    matrix: Any
    generation: tuple[int, int, int] | None


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
        self._model_loaded = threading.Event()
        self._warned: set[str] = set()
        self._snapshot = _IndexSnapshot((), None, None)
        self._vec_cache: dict[str, Any] = {}  # sha256(text) -> ndarray
        self.available = False
        self._worker: threading.Thread | None = None
        self._pending: tuple[Any, Any] | None = None
        self._requested: tuple[Any, str, int] | None = None
        self._published: tuple[Any, str, int] | None = None
        self._epoch = 0
        self._closed = False

    # ── warnings (once per failure class, never per turn) ───────────────

    def _warn_once(self, key: str, msg: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        logger.warning("%s", msg)

    # ── model lifecycle ─────────────────────────────────────────────────

    def _load_model(self) -> Any:
        """Load our thin ONNX embedder off-turn. Caller holds no lock."""
        from tools.memory_embedder import LocalTextEmbedding

        cache_dir = _model_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        model = LocalTextEmbedding(cache_dir=str(cache_dir))
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
                logger.debug("local ONNX load failure detail", exc_info=True)
                self._model_loaded.set()
                return
            with _STATE_LOCK:
                self._model = model
                self._model_state = _READY
            self._model_loaded.set()

        logger.info(
            "semantic recall model loading %s in background (hybrid BM25 fill serves until ready)",
            MODEL_NAME,
        )
        threading.Thread(target=_worker, name="memory-semantic-model", daemon=True).start()

    def _get_model(self) -> Any:
        """Return the loaded model, or None; never raises into the turn.

        Both the cold download and the warm re-load (lazy ONNX import +
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
        """Queue the latest immutable input; never build or wait on the caller.

        Text participates in identity because provenance/closed-value filtering
        can change the admitted rows without changing the on-disk signature.
        Failed builds keep lexical fallback and retry only when this input
        identity changes; identical requests never spin or retry the failure.
        """
        documents = tuple((str(row.get("key") or ""), _doc_text(row)) for row in rows)
        fingerprint = _text_hash(repr(documents))
        with self._lock:
            if self._closed:
                return False
            request = (signature, fingerprint, self._epoch)
            if request == self._published:
                return self._snapshot.matrix is not None
            if request != self._requested:
                self._requested = request
                self._pending = (documents, request)
            if not documents:
                self._snapshot = _IndexSnapshot((), None, signature)
                self._published = request
                self._pending = None
                return False
            if self._pending is not None and (self._worker is None or not self._worker.is_alive()):
                self._worker = threading.Thread(
                    target=self._build_pending, name="memory-semantic-index", daemon=True,
                )
                self._worker.start()
        return False

    def _build_pending(self) -> None:
        """Single writer: model loading, embeddings and cache IO stay off-turn."""
        while True:
            with self._lock:
                pending = self._pending
                self._pending = None
                if pending is None or self._closed:
                    self._worker = None
                    return
            documents, request = pending
            signature, fingerprint, epoch = request
            try:
                # Model initialization and index construction share this worker.
                # _get_model remains a nonblocking public/test seam.
                # A new input also permits recovery from a failed model load.
                with _STATE_LOCK:
                    if self._model_state == _FAILED:
                        self._model_loaded.clear()
                        self._model_state = _UNATTEMPTED
                model = self._get_model()
                if model is None:
                    with _STATE_LOCK:
                        state = self._model_state
                    if state == _LOADING:
                        # Existing loader owns initialization; it signals us
                        # when ready rather than making a recall poll for it.
                        self._model_loaded.wait()
                        model = self._get_model()
                if model is None:
                    continue
                import numpy as np

                if not self._vec_cache:
                    self._vec_cache = self._load_vec_cache()
                keys, texts = zip(*documents)
                hashes = [_text_hash(text) for text in texts]
                missing = list(dict.fromkeys(text for text, h in zip(texts, hashes) if h not in self._vec_cache))
                if missing:
                    # Bound ONNX working sets to one document at a time. Do not
                    # materialize an additional batch of output vectors.
                    fresh = iter(model.embed(missing, batch_size=1))
                    for text in missing:
                        self._vec_cache[_text_hash(text)] = np.asarray(next(fresh), dtype=np.float32)
                    if next(fresh, None) is not None:
                        raise RuntimeError("embedder returned too many vectors")
                    self._save_vec_cache()
                matrix = np.stack([self._vec_cache[h] for h in hashes]).astype(np.float32)
                norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                matrix /= norms
                matrix.setflags(write=False)
                with self._lock:
                    if not self._closed and epoch == self._epoch and request == self._requested:
                        self._snapshot = _IndexSnapshot(tuple(keys), matrix, signature)
                        self._published = request
            except Exception:
                self._warn_once("index-build", "semantic recall disabled: index build failed; keeping hybrid BM25 fill")
                logger.debug("semantic index build failure detail", exc_info=True)

    def search(
        self, query: str, limit: int = SEMANTIC_FETCH,
        *, expected: tuple[Any, str, int] | None = None,
    ) -> list[tuple[str, float]]:
        """Return ``[(key, cosine)]`` best-first; empty on any failure.

        Use the latest complete generation captured after query embedding.
        A rebuild or close after capture cannot change this search's pairing.
        """
        if self._closed or self._snapshot.matrix is None:
            return []
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
                if expected is not None and expected != self._published:
                    return []
                snapshot = self._snapshot
            if snapshot.matrix is None or not snapshot.keys:
                return []
            sims = (snapshot.matrix @ qvec).tolist()
            ranked = sorted(zip(snapshot.keys, sims), key=lambda item: (-item[1], item[0]))[:limit]
            return [(str(key), float(score)) for key, score in ranked]
        except Exception:
            self._warn_once("search", "semantic recall degraded: query embedding failed")
            logger.debug("semantic search failure detail", exc_info=True)
            return []

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._epoch += 1
            self._pending = None
            self._snapshot = _IndexSnapshot((), None, None)
            self._model_loaded.set()


_GLOBAL_INDEX = SemanticTopicIndex()


def global_index() -> SemanticTopicIndex:
    return _GLOBAL_INDEX


def schedule_session_warm() -> None:
    """Schedule store IO and index admission off the session-init caller."""
    index = global_index()

    def warm() -> None:
        try:
            from tools.memory_hybrid import store_signature
            from tools.topic_memory_store import load_topics, topic_memory_path

            rows = load_topics()
            stat = topic_memory_path().stat()
            index.ensure(rows, store_signature(rows, mtime_ns=stat.st_mtime_ns, size=stat.st_size))
        except Exception:
            logger.debug("semantic session warm unavailable; keeping hybrid BM25 fill", exc_info=True)

    threading.Thread(target=warm, name="memory-semantic-warm", daemon=True).start()


def reset_global_index() -> None:
    """Test seam: replace the singleton (model, vectors, warnings) entirely."""
    global _GLOBAL_INDEX
    with _STATE_LOCK:
        _GLOBAL_INDEX.close()
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
    documents = tuple((str(row.get("key") or ""), _doc_text(row)) for row in topics)
    expected = (signature, _text_hash(repr(documents)), index._epoch)
    return index.search(clean, limit=top_k, expected=expected)
