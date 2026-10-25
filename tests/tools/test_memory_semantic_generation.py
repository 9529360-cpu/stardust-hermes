"""Generation consistency through real index rebuild/search, without ONNX."""

import concurrent.futures
import threading

import pytest

import tools.memory_semantic as ms


class Embeddings:
    def embed(self, texts, **kwargs):
        return [[1.0, 0.0] if "alpha" in text else [0.0, 1.0] for text in texts]

    def query_embed(self, query):
        return iter([[1.0, 0.0]])


ROWS = [{"key": key, "title": key} for key in ("alpha", "beta")]


@pytest.fixture
def index(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    idx = ms.SemanticTopicIndex()
    idx._model = Embeddings()
    idx._model_state = ms._READY
    return idx


def test_rebuild_after_search_unlock_keeps_scores_with_original_keys(index):
    """Port C10.4's unlock schedule, but rebuild via ensure, not field writes."""
    real_lock = threading.Lock()

    class RebuildOnExit:
        rebuild = None

        def __enter__(self):
            real_lock.acquire()

        def __exit__(self, *args):
            real_lock.release()
            rebuild, self.rebuild = self.rebuild, None
            if rebuild:
                rebuild()

    lock = RebuildOnExit()
    index._lock = lock
    wrong = 0
    for i in range(120):
        assert index.ensure(ROWS, (i * 2, 0, 2))
        lock.rebuild = lambda i=i: index.ensure(ROWS[::-1], (i * 2 + 1, 0, 2))
        result = index.search("alpha")
        wrong += result != [("alpha", 1.0), ("beta", 0.0)]
    assert wrong == 0, f"wrong-key results: {wrong}/120"


def test_unforced_concurrent_search_and_rebuild(index):
    assert index.ensure(ROWS, (0, 0, 2))
    start = threading.Barrier(2)

    def rebuild():
        start.wait(timeout=10)
        for i in range(1200):
            assert index.ensure(ROWS if i % 2 else ROWS[::-1], (i + 1, 0, 2))

    def search():
        start.wait(timeout=10)
        return [index.search("alpha") for _ in range(1200)]

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        writer = pool.submit(rebuild)
        results = pool.submit(search).result(timeout=30)
        writer.result(timeout=30)
    wrong = sum(result != [("alpha", 1.0), ("beta", 0.0)] for result in results)
    assert wrong == 0, f"wrong-key results: {wrong}/1200"


def test_published_generation_survives_rebuild_failure_empty_and_close(index):
    assert index.ensure(ROWS, (1, 0, 2))
    original = index._snapshot
    assert original.keys == ("alpha", "beta")
    assert original.generation == (1, 0, 2)
    with pytest.raises(ValueError):
        original.matrix[0, 0] = 0

    assert index.ensure(ROWS[::-1], (2, 0, 2))
    assert index._snapshot.keys == ("beta", "alpha")
    assert original.keys == ("alpha", "beta")
    assert original.matrix.tolist() == [[1.0, 0.0], [0.0, 1.0]]

    class Broken(Embeddings):
        def embed(self, texts, **kwargs):
            raise RuntimeError("embedding failed")

    published = index._snapshot
    index._model = Broken()
    assert not index.ensure([{"key": "new", "title": "new"}], (3, 0, 1))
    assert index._snapshot is published
    assert index.search("alpha")[0] == ("alpha", 1.0)
    assert not index.ensure([], (4, 0, 0))
    assert index.search("alpha") == []
    assert index._snapshot.generation == (4, 0, 0)
    index.close()
    assert index.search("alpha") == []
    assert index._snapshot.generation is None
