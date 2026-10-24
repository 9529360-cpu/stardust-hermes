"""Opt-in hybrid BM25+lexical topic recall (DL-P2.8b), pure local.

Builds a tiny in-memory SQLite FTS5 index over topic title+keywords+summary
and fuses the BM25 rank list with the existing lexical rank list by
Reciprocal Rank Fusion (Cormack et al., SIGIR 2009; k=60). No network, no
model calls, no vector DB; the index is a process-local cache that is fully
rebuildable from TOPICS.json.

Tokenizer: documents and queries are pre-tokenized here (Latin/digit runs
lowercased whole, CJK segments expanded to overlapping bigrams, the same
lesson ``hermes_state_fts.py`` learned when it replaced ``trigram`` — FTS5
trigram needs >=3 chars per term, so 2-char Chinese words like 米兰/手机
would never match). The table itself uses the always-available ``unicode61``
tokenizer over that pre-tokenized space-joined text. If FTS5 is missing
entirely, ``ensure()`` reports unusable and callers silently fall back to
lexical-only scoring (step a).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from typing import Any, Iterable

logger = logging.getLogger(__name__)

RRF_K = 60  # Cormack et al. 2009
FTS_TABLE = "topics_fts"
_INDEXED_FIELDS = ("title", "keywords", "summary")
# How many rows to pull from the FTS rank list before fusing; the recall cap
# (limit) is applied by the caller after fusion.
FTS_FETCH = 24
# BM25-only candidates (below the lexical gate) admitted into fusion by rank
# position: enough headroom that a summary-only match survives, few enough
# that near-miss documents cannot crowd out lexical hits.
FTS_ADMIT = 8
# BM25 score gate for those candidates, as a fraction of the best row's
# bm25() (negative; more negative = better, and the magnitude is corpus-size
# dependent — small stores drive every IDF toward 0, so an absolute cutoff
# silently disables the fill). A row at least this strong relative to the
# leader may fill the starved recall; filler-word matches stay out.
EXTRA_ADMIT_FRACTION = 0.15
# When the lexical list is completely empty, BM25 fills exactly ONE recall
# slot: its top score-gated row. Precision falls off steeply after the first
# BM25 row, and a second guess would only add irrelevant injections.
EXTRA_FILL_SLOTS = 1

# Latin/digit runs joined across single spaces into one phrase token
# ("VS Code"), so a quoted FTS5 phrase matches documents word-for-word.
_LATIN_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*(?: [A-Za-z0-9._+-]+)*")
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")


def _bigram_tokens(text: str) -> list[str]:
    """Latin/digit phrases kept whole (lowercased); CJK runs expanded to
    bigrams PLUS every single char.

    Bigrams alone lose the rare information-carrying chars of a paraphrase
    (请阿豪吃饭 → 的主/是什 noise bigrams score as high as real ones); the
    unigrams give BM25's IDF the rare chars (吃/饭/主/食) that actually
    identify the document, while filler chars (的/是/么) stay low-IDF.
    """
    text = str(text or "")
    tokens = [run.lower() for run in _LATIN_RUN.findall(text)]
    for segment in _CJK_RUN.findall(text):
        if len(segment) == 1:
            tokens.append(segment)
            continue
        tokens.extend(segment[i : i + 2] for i in range(len(segment) - 1))
        tokens.extend(segment)
    return tokens


def _tokenized(text: str) -> str:
    return " ".join(_bigram_tokens(text))


def _fts_match_expr(query: str) -> str:
    """OR-match: classic BM25 retrieval semantics.

    Implicit AND would require every query token (incl. question words like
    什么 that never appear in summaries) to hit the document, emptying the
    result; OR lets IDF rank documents by the rare terms they do match.
    Every token is quoted so user text is never read as FTS5 operators.
    """
    tokens = _bigram_tokens(query)
    if not tokens:
        return ""
    return " OR ".join(f'"{token}"' for token in tokens)


def rrf_fuse(
    *rank_lists: Iterable[tuple[str, float]],
    k: int = RRF_K,
) -> list[tuple[str, float]]:
    """Fuse rank lists by Reciprocal Rank Fusion.

    Each list is ``[(key, score)]`` ordered best-first; only the order is
    used. Returns ``[(key, fused_score)]`` best-first. Fused-score ties are
    broken by the better position in the most recent rank list (the BM25
    list outranks the lexical list on agreement rows), then by key, so the
    result is deterministic.
    """
    scores: dict[str, float] = {}
    latest_pos: dict[str, int] = {}
    for rank_list in rank_lists:
        for position, item in enumerate(rank_list):
            key = str(item[0]).strip()
            if not key:
                continue
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + position + 1)
            latest_pos[key] = position
    return sorted(
        scores.items(),
        key=lambda item: (-item[1], latest_pos[item[0]], item[0]),
    )


class HybridTopicIndex:
    """In-memory FTS5 index over the topic store; rebuilt on store change.

    The store is bounded (<=64 topics by design), so the index lives in
    memory and a rebuild is a full refresh guarded by the store signature
    ``(mtime_ns, size, row_count)``. All work happens under one lock; query
    uses the same connection guarded by ``check_same_thread=False`` plus the
    lock to stay safe for the recall path.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._keys: list[str] = []
        self._signature: tuple[int, int, int] | None = None
        self.available = False
        self.tokenizer = ""

    def ensure(self, rows: list[dict[str, Any]], signature: tuple[int, int, int]) -> bool:
        """Rebuild when ``signature`` changed; True when the index is usable."""
        with self._lock:
            if self.available and signature == self._signature:
                return True
            self._close_locked()
            if not rows:
                # Nothing to index: not an error, simply unusable (lexical
                # scoring over an empty store already returns nothing).
                self._signature = signature
                return False
            conn = self._build(rows)
            if conn is None:
                return False
            self._conn = conn
            self._keys = [str(row.get("key") or "") for row in rows]
            self._signature = signature
            self.available = True
            return True

    def _build(self, rows: list[dict[str, Any]]) -> sqlite3.Connection | None:
        try:
            conn = sqlite3.connect(":memory:", check_same_thread=False)
        except sqlite3.Error as exc:
            logger.debug("hybrid index: cannot open sqlite (%s)", exc)
            return None
        try:
            conn.execute(
                f"CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5("
                f"{', '.join(_INDEXED_FIELDS)}, tokenize='unicode61')"
            )
            conn.executemany(
                f"INSERT INTO {FTS_TABLE}(rowid, {', '.join(_INDEXED_FIELDS)}) VALUES (?, ?, ?, ?)",
                [
                    (
                        index,
                        _tokenized(row.get("title")),
                        _tokenized(" ".join(str(k) for k in row.get("keywords") or [])),
                        _tokenized(row.get("summary")),
                    )
                    for index, row in enumerate(rows)
                ],
            )
            conn.commit()
        except sqlite3.Error as exc:
            # No FTS5 (or tokenizer) in this build: stay silent, caller uses
            # lexical-only scoring.
            logger.debug("hybrid index: FTS5 unavailable (%s)", exc)
            try:
                conn.close()
            except sqlite3.Error:
                pass
            return None
        self.tokenizer = "unicode61+bigram"
        return conn

    def search(self, query: str, limit: int = FTS_FETCH) -> list[tuple[str, float]]:
        """BM25 rank list ``[(key, bm25)]`` best-first for the query."""
        with self._lock:
            conn = self._conn
            if conn is None or limit <= 0:
                return []
            match = _fts_match_expr(query)
            if not match:
                return []
            try:
                cursor = conn.execute(
                    f"SELECT rowid, bm25({FTS_TABLE}) FROM {FTS_TABLE} "
                    f"WHERE {FTS_TABLE} MATCH ? ORDER BY 2 LIMIT ?",
                    (match, limit),
                )
                fetched = cursor.fetchall()
            except sqlite3.Error as exc:
                logger.debug("hybrid index: query failed (%s)", exc)
                return []
            out: list[tuple[str, float]] = []
            for rowid, score in fetched:
                if 0 <= rowid < len(self._keys):
                    out.append((self._keys[rowid], float(score)))
            return out

    def _close_locked(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
        self._conn = None
        self._keys = []
        self.available = False
        self.tokenizer = ""

    def close(self) -> None:
        with self._lock:
            self._close_locked()
            self._signature = None


_GLOBAL_INDEX = HybridTopicIndex()


def global_index() -> HybridTopicIndex:
    """Process-wide index reused across recalls in the same agent process."""
    return _GLOBAL_INDEX


def reset_global_index() -> None:
    """Drop the cached index (tests / store resets)."""
    _GLOBAL_INDEX.close()


def store_signature(rows: list[dict[str, Any]], *, mtime_ns: int, size: int) -> tuple[int, int, int]:
    return (mtime_ns, size, len(rows))
