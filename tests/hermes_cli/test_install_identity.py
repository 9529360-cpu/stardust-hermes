from concurrent.futures import ThreadPoolExecutor
import errno
import multiprocessing
from pathlib import Path
import threading
import time

from gateway.hosted_rooms import local_authority_gateway_id
import hermes_cli.install_identity as install_identity
from hermes_cli.install_identity import read_or_create_install_id


def _race_first_install_id(
    root_value,
    minted,
    results,
    start_barrier=None,
    writer_entered=None,
    release_writer=None,
):
    root = Path(root_value)
    install_identity.uuid.uuid4 = lambda: type("FixedUuid", (), {"hex": minted})()
    if start_barrier is not None:
        start_barrier.wait(timeout=10)
    if writer_entered is not None:
        import utils
        original_mkstemp = utils.tempfile.mkstemp

        def held_mkstemp(*args, **kwargs):
            writer_entered.set()
            assert release_writer.wait(timeout=10)
            return original_mkstemp(*args, **kwargs)

        utils.tempfile.mkstemp = held_mkstemp
    results.put(read_or_create_install_id(root))


def _hold_install_id_publication_lock(root_value, entered, release):
    root = Path(root_value)
    root.mkdir(parents=True, exist_ok=True)
    with install_identity._install_id_file_lock(root, timeout=2.0):
        entered.set()
        assert release.wait(timeout=10)


def test_concurrent_first_use_returns_one_persisted_identity(tmp_path):
    with ThreadPoolExecutor(max_workers=16) as executor:
        values = list(executor.map(lambda _: read_or_create_install_id(tmp_path), range(64)))

    assert len(set(values)) == 1
    assert values[0]
    assert (tmp_path / "install_id").read_text(encoding="utf-8").strip() == values[0]


def test_independent_first_callers_return_the_single_committed_identity(tmp_path, monkeypatch):
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    writer_entered = context.Event()
    release_writer = context.Event()
    winner = context.Process(
        target=_race_first_install_id,
        args=(
            str(tmp_path),
            "a" * 32,
            results,
            None,
            writer_entered,
            release_writer,
        ),
    )
    loser = context.Process(
        target=_race_first_install_id,
        args=(str(tmp_path), "b" * 32, results),
    )

    winner.start()
    assert writer_entered.wait(timeout=10)
    loser.start()
    time.sleep(0.25)
    assert loser.is_alive()
    release_writer.set()
    processes = [winner, loser]
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    returned = [results.get(timeout=2) for _ in processes]
    persisted = (tmp_path / "install_id").read_text(encoding="utf-8").strip()

    assert returned == [persisted, persisted]

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        install_identity,
        "_INSTALL_ID_CACHE",
        {"root": None, "value": None},
    )
    assert local_authority_gateway_id() == f"install:{persisted}"


def test_concurrent_corrupt_file_repair_returns_one_committed_identity(tmp_path):
    (tmp_path / "install_id").write_text("corrupt\n", encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_race_first_install_id,
            args=(str(tmp_path), value, results, barrier),
        )
        for value in ("a" * 32, "b" * 32)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    returned = [results.get(timeout=2) for _ in processes]
    persisted = (tmp_path / "install_id").read_text(encoding="utf-8").strip()

    assert returned == [persisted, persisted]


def test_contended_publication_lock_times_out_without_minting(tmp_path, monkeypatch):
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_install_id_publication_lock,
        args=(str(tmp_path), entered, release),
    )
    holder.start()
    assert entered.wait(timeout=10)
    monkeypatch.setattr(install_identity, "_INSTALL_ID_FILE_LOCK_TIMEOUT_S", 0.1)

    started = time.monotonic()
    assert read_or_create_install_id(tmp_path) is None
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert not (tmp_path / "install_id").exists()
    release.set()
    holder.join(timeout=10)
    assert holder.exitcode == 0

    committed = read_or_create_install_id(tmp_path)
    assert committed
    assert (tmp_path / "install_id").read_text(encoding="utf-8").strip() == committed


def test_cache_lock_does_not_span_slow_authority_io(tmp_path, monkeypatch):
    stable = "a" * 32
    first_entered = threading.Event()
    release_first = threading.Event()
    call_lock = threading.Lock()
    calls = 0

    monkeypatch.setattr(install_identity, "get_default_hermes_root", lambda: tmp_path)

    def controlled_resolve(root):
        nonlocal calls
        assert root == tmp_path
        with call_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            first_entered.set()
            assert release_first.wait(timeout=10)
        return stable

    monkeypatch.setattr(install_identity, "read_or_create_install_id", controlled_resolve)
    cache: dict[str, str | None] = {"root": None, "value": None}

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(install_identity.get_install_id, cache=cache)
        assert first_entered.wait(timeout=2)
        second = executor.submit(install_identity.get_install_id, cache=cache)
        assert second.result(timeout=2) == stable
        release_first.set()
        assert first.result(timeout=2) == stable

    assert cache == {"root": str(tmp_path), "value": stable}


def test_lock_error_classification_retries_only_contention():
    assert install_identity._is_lock_contention_errno(OSError(errno.EAGAIN, "busy"))
    assert install_identity._is_lock_contention_errno(OSError(errno.EACCES, "busy"))
    assert not install_identity._is_lock_contention_errno(OSError(errno.ENOSPC, "disk full"))
    assert not install_identity._is_lock_contention_errno(OSError(errno.EMFILE, "too many files"))
