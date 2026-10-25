"""Caller latency and publication barriers for background semantic builds."""
import threading
import time

import pytest

import tools.memory_semantic as ms


def wait_ready(index, rows, signature):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if index.ensure(rows, signature):
            return
        time.sleep(.001)
    pytest.fail("background index did not publish")


@pytest.fixture
def index(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    idx = ms.SemanticTopicIndex()
    yield idx
    idx.close()


def test_build_never_holds_caller_and_coalesces_latest_generation(index):
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Adapter:
        def embed(self, texts, **kwargs):
            calls.append(list(texts))
            entered.set()
            assert release.wait(5)
            return [[1., 0.] for _ in texts]

        def query_embed(self, query):
            return iter([[1., 0.]])

    index._model, index._model_state = Adapter(), ms._READY
    old = [{"key": "old", "title": "old"}]
    latest = [{"key": "latest", "title": "latest"}]
    try:
        start = time.monotonic()
        assert not index.ensure(old, (1, 1, 1))
        assert time.monotonic() - start < .05
        assert entered.wait(2)
        for _ in range(20):
            start = time.monotonic()
            assert not index.ensure(latest, (2, 1, 1))
            assert time.monotonic() - start < .05
        assert index.search("anything") == []
        # Mutating caller-owned data cannot change a queued build.
        latest[0]["title"] = "mutated"
    finally:
        release.set()
    wait_ready(index, [{"key": "latest", "title": "latest"}], (2, 1, 1))
    assert calls == [["old"], ["latest"]]
    assert index.search("anything") == [("latest", 1.)]


def test_same_signature_changed_content_does_not_reuse_old_index(index):
    class Adapter:
        def embed(self, texts, **kwargs):
            return [[1., 0.] if "alpha" in text else [0., 1.] for text in texts]

        def query_embed(self, query):
            return iter([[1., 0.]])

    index._model, index._model_state = Adapter(), ms._READY
    rows = [{"key": "same", "title": "alpha"}]
    signature = (1, 1, 1)
    wait_ready(index, rows, signature)
    original = index._published
    assert index.search("alpha", expected=original) == [("same", 1.)]
    changed = [{"key": "same", "title": "beta"}]
    assert not index.ensure(changed, signature)
    wait_ready(index, changed, signature)
    assert index.search("alpha", expected=original) == []
    assert index.search("alpha") == [("same", 0.)]


@pytest.mark.parametrize("failure", ["embed", "load"])
def test_failed_build_retries_only_after_input_change(index, monkeypatch, failure):
    attempts = []

    class Adapter:
        def embed(self, texts, **kwargs):
            if failure == "embed":
                attempts.append("embed")
                if len(attempts) == 1:
                    raise RuntimeError("transient embedding failure")
            return [[1., 0.] for _ in texts]

        def query_embed(self, query):
            return iter([[1., 0.]])

    def load():
        attempts.append("load")
        if len(attempts) == 1:
            raise RuntimeError("transient model failure")
        return Adapter()

    if failure == "load":
        monkeypatch.setattr(index, "_load_model", load)
    else:
        index._model, index._model_state = Adapter(), ms._READY
    rows = [{"key": "same", "title": "first"}]
    assert not index.ensure(rows, (1, 1, 1))
    worker = index._worker
    if worker is not None:
        worker.join(5)
        assert not worker.is_alive()
    assert len(attempts) == 1
    for _ in range(20):
        assert not index.ensure(rows, (1, 1, 1))
    assert index._worker is None
    assert len(attempts) == 1
    # Content changes count even when the disk signature stays the same.
    wait_ready(index, [{"key": "same", "title": "changed"}], (1, 1, 1))
    assert len(attempts) == 2
    assert index.search("same") == [("same", 1.)]


def test_session_warm_reads_store_off_caller(index, monkeypatch):
    import tools.topic_memory_store as tms

    entered, release = threading.Event(), threading.Event()
    caller = threading.get_ident()
    rows = [{"key": "warm", "title": "warm"}]

    def load():
        assert threading.get_ident() != caller
        entered.set()
        assert release.wait(5)
        return rows

    class Adapter:
        def embed(self, texts, **kwargs):
            return [[1., 0.] for _ in texts]

    index._model, index._model_state = Adapter(), ms._READY
    monkeypatch.setattr(ms, "_GLOBAL_INDEX", index)
    monkeypatch.setattr(tms, "load_topics", load)
    path = tms.topic_memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    try:
        start = time.monotonic()
        ms.schedule_session_warm()
        assert time.monotonic() - start < .05
        assert entered.wait(2)
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while index._snapshot.matrix is None and time.monotonic() < deadline:
        time.sleep(.001)
    assert index._snapshot.keys == ("warm",)


def test_close_invalidates_inflight_publication(index):
    entered, release = threading.Event(), threading.Event()

    class Adapter:
        def embed(self, texts, **kwargs):
            entered.set()
            assert release.wait(5)
            return [[1., 0.]]

    index._model, index._model_state = Adapter(), ms._READY
    try:
        assert not index.ensure([{"key": "old", "title": "old"}], (1, 1, 1))
        assert entered.wait(2)
        worker = index._worker
        index.close()
    finally:
        release.set()
    worker.join(5)
    assert not worker.is_alive()
    assert index._snapshot.generation is None
    assert index.search("anything") == []
