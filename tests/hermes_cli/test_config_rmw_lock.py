"""Concurrent dashboard config writers must not drop each other's mutations.

Only ``PUT /api/config`` (and the ``config_write_scope`` routers) held ``_CONFIG_MUTATION_LOCK``.
The custom-endpoint handlers, the profile-create model write, ``POST /api/model/set`` and
``PUT /api/model/moa`` ran their load→mutate→save cycles on worker threads without it, so a
writer racing the desktop's debounced whole-record autosave interleaved as::

    T1 load (providers={})     T2 load (providers={})
    T1 mutate providers.box    T2 mutate display.x
    T1 save (providers.box)    T2 save (providers={} + display.x)   <- T1's write erased

Both tests force exactly that interleaving and assert both writes land.
"""

from __future__ import annotations

import pathlib
import threading
import time

import pytest
import yaml


@pytest.fixture
def client(monkeypatch, _isolate_hermes_home):
    from starlette.testclient import TestClient

    from hermes_cli.config import load_config, save_config
    from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN, app

    monkeypatch.setattr("hermes_cli.model_cost_guard.expensive_model_warning", lambda *_a, **_k: None)
    cfg = load_config()
    cfg["model"] = {"provider": "openrouter", "default": "openai/gpt-5.5"}
    save_config(cfg)

    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return client


def _race_second_writer_into_first_writers_save(monkeypatch, first, second, timeout: float = 5.0):
    """Deterministic lost-write interleaving: ``first`` runs until it reaches ``save_config``,
    then ``second`` is started and ``first`` waits (up to ``timeout``) for ``second`` to
    ``load_config`` before saving. Unlocked, ``second`` loads the stale document and its save
    erases ``first``'s mutation. With the RMW lock ``second`` blocks before its load, ``first``'s
    wait times out, and the two writes serialize. Returns ``(first_response, second_response)``."""
    import hermes_cli.config as cfg_mod

    real_save, real_load = cfg_mod.save_config, cfg_mod.load_config
    first_at_save, second_loaded = threading.Event(), threading.Event()
    gate = threading.Lock()

    def gated_save(config, *args, **kwargs):
        # The first save_config call in the test is the first writer's (the second has not
        # started yet). Hold it until the second writer has loaded — or the lock kept it out.
        if not first_at_save.is_set():
            first_at_save.set()
            second_loaded.wait(timeout)
            with gate:
                return real_save(config, *args, **kwargs)
        return real_save(config, *args, **kwargs)

    def spied_load(*args, **kwargs):
        cfg = real_load(*args, **kwargs)
        # Any load while the first writer sits blocked in save_config is the second writer's.
        if first_at_save.is_set() and not gate.locked():
            second_loaded.set()
        return cfg

    monkeypatch.setattr(cfg_mod, "save_config", gated_save)
    monkeypatch.setattr(cfg_mod, "load_config", spied_load)

    results: list = [None, None]
    threads = [threading.Thread(target=lambda: results.__setitem__(0, first())),
               threading.Thread(target=lambda: results.__setitem__(1, second()))]
    threads[0].start()
    assert first_at_save.wait(30), "first writer never reached save_config"
    threads[1].start()
    for t in threads:
        t.join(timeout=60)
    return results


def _on_disk() -> dict:
    from hermes_constants import get_hermes_home
    return yaml.safe_load((get_hermes_home() / "config.yaml").read_text(encoding="utf-8"))


def test_custom_endpoint_upsert_racing_config_autosave_keeps_both_writes(client, monkeypatch):
    """Saving a custom endpoint (sync-def handler on a worker thread) while the settings-page
    autosave (PUT /api/config) is in flight: the new ``providers`` entry AND the autosaved field
    both survive."""
    autosave = lambda: client.put(  # noqa: E731
        "/api/config", json={"config": {"display": {"personality": "canary"}}})
    upsert = lambda: client.post(  # noqa: E731
        "/api/providers/custom-endpoints",
        json={"id": "racebox", "name": "racebox", "base_url": "http://racebox:8000/v1", "model": "race-model",
              "discover_models": False})

    r1, r2 = _race_second_writer_into_first_writers_save(monkeypatch, autosave, upsert)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text

    on_disk = _on_disk()
    assert "racebox" in on_disk["providers"]
    assert on_disk["display"]["personality"] == "canary"


def test_custom_endpoint_activate_racing_moa_save_keeps_both_writes(client, monkeypatch):
    """Two worker-thread writers (custom-endpoint activate vs MoA save) serialize through the
    same lock — the ``model`` switch and the ``moa`` section are both on disk afterwards."""
    from hermes_cli.config import load_config, save_config

    cfg = load_config()
    cfg["providers"] = {"racebox": {"base_url": "http://racebox:8000/v1", "model": "race-model", "api_key": "k"}}
    save_config(cfg)

    activate = lambda: client.post("/api/providers/custom-endpoints/racebox/activate")  # noqa: E731
    moa = lambda: client.put(  # noqa: E731
        "/api/model/moa",
        json={"reference_models": [{"provider": "openrouter", "model": "openai/gpt-5.5"}],
              "aggregator": {"provider": "openrouter", "model": "openai/gpt-5.5"}})

    r1, r2 = _race_second_writer_into_first_writers_save(monkeypatch, activate, moa)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text

    on_disk = _on_disk()
    assert on_disk["model"]["provider"] == "racebox"
    assert on_disk["moa"]["aggregator"]["model"] == "openai/gpt-5.5"


def test_config_set_cross_process_writes_do_not_drop_siblings(tmp_path):
    """Independent CLI writers must serialize the full config read-modify-write."""
    import os
    import subprocess
    import sys

    config_path = tmp_path / "config.yaml"
    config_path.write_text("seed: keep\n", encoding="utf-8")
    gate = tmp_path / "go"
    script = r'''
import os, pathlib, sys, time
import hermes_cli.config as config
home = pathlib.Path(os.environ["HERMES_HOME"])
index = sys.argv[1]
(home / f"ready-{index}").write_text("1", encoding="utf-8")
while not (home / "go").exists():
    time.sleep(0.01)
config.set_config_value(f"race_{index}", index, force=True)
'''
    env = dict(os.environ, HERMES_HOME=str(tmp_path))
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(index)],
            cwd=pathlib.Path(__file__).resolve().parents[2],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for index in range(8)
    ]
    deadline = time.time() + 20
    while sum((tmp_path / f"ready-{index}").exists() for index in range(8)) < 8:
        assert time.time() < deadline, "config writers did not reach the start gate"
        time.sleep(0.02)
    gate.write_text("1", encoding="utf-8")
    results = [process.communicate(timeout=30) for process in processes]
    assert [process.returncode for process in processes] == [0] * 8, results

    on_disk = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert on_disk["seed"] == "keep"
    assert {f"race_{index}": on_disk.get(f"race_{index}") for index in range(8)} == {
        f"race_{index}": index for index in range(8)
    }


def test_dashboard_lock_serializes_with_cli_config_writer(tmp_path):
    """Dashboard RMW lock must coordinate with config CLI processes, not only threads."""
    import os
    import subprocess
    import sys

    config_path = tmp_path / "config.yaml"
    config_path.write_text("seed: keep\n", encoding="utf-8")
    repo = pathlib.Path(__file__).resolve().parents[2]
    env = dict(os.environ, HERMES_HOME=str(tmp_path))
    web_script = r'''
import os, pathlib, time
from hermes_cli import web_server
from hermes_cli.config import require_readable_config_before_write
from utils import atomic_yaml_write
home = pathlib.Path(os.environ["HERMES_HOME"])
path = home / "config.yaml"
with web_server._CONFIG_MUTATION_LOCK:
    data = require_readable_config_before_write(path)
    data["dashboard"] = "kept"
    (home / "dashboard-ready").write_text("1", encoding="utf-8")
    while not (home / "cli-attempt").exists():
        time.sleep(0.01)
    time.sleep(0.75)
    atomic_yaml_write(path, data, sort_keys=False)
'''
    cli_script = r'''
import os, pathlib
import hermes_cli.config as config
home = pathlib.Path(os.environ["HERMES_HOME"])
(home / "cli-attempt").write_text("1", encoding="utf-8")
config.set_config_value("cli", "kept", force=True)
'''
    web = subprocess.Popen([sys.executable, "-c", web_script], cwd=repo, env=env)
    deadline = time.time() + 20
    while not (tmp_path / "dashboard-ready").exists():
        assert web.poll() is None and time.time() < deadline
        time.sleep(0.02)
    cli = subprocess.Popen([sys.executable, "-c", cli_script], cwd=repo, env=env)
    assert web.wait(timeout=30) == 0
    assert cli.wait(timeout=30) == 0

    on_disk = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert on_disk == {"seed": "keep", "dashboard": "kept", "cli": "kept"}
