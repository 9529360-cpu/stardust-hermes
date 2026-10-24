"""DL-P2.5 real-restart scenario: one real gateway process, loopback fake model.

Spawns a real gateway (api_server platform, free port) in a temp HOME with
digital_life.auto_resume=true, drives it over HTTP: set a whitelisted goal
(/goal autoresume on) whose work appends lines to a counter file, hard-kills
the process mid-goal (a watcher kills as soon as the first append lands),
restarts it, and verifies the goal resumed BY ITSELF and finished exactly once.

No paid model: the gateway's chat model AND the goal judge both point at a
loopback FakeModel scripted to do the file work (read-then-write; the
file-mutation verifier refuses blind overwrites) and and to answer the judge
with a done verdict only once the counter file holds the finished marker.

Usage: python evals/digital_life/auto_resume_real.py [--keep]
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

COUNTER = "counter.txt"
FINAL_LINE = "TASK-DONE"


def http(method: str, url: str, key: str, body: dict | None = None, timeout: float = 30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def wait_health(port: str, key: str, timeout: float = 60) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            http("GET", f"http://127.0.0.1:{port}/health", key, timeout=3)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    from evals.assistant_scenarios.fake_model import FakeModel, Reply

    home = Path(os.environ.get("DL_P25_REAL_HOME", REPO / "evals/digital_life/.auto_resume_real_tmp"))
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True)
    counter_path = home / COUNTER

    def counter_text() -> str:
        return counter_path.read_text(encoding="utf-8") if counter_path.exists() else ""

    def script(request):
        text = request.text()
        # Goal judge calls carry the judge user prompt; answer from the counter file.
        if "Is the goal satisfied" in text:
            done = FINAL_LINE in counter_text()
            return Reply(text=json.dumps({
                "verdict": "done" if done else "continue",
                "reason": "counter file holds the finished marker" if done else "work not finished",
            }))
        # Worker: one append per turn (read-then-write; the verifier refuses blind
        # overwrites). Three appends, then the final marker. The kill lands after
        # the second append — goal clearly unfinished, resume must finish it.
        current = counter_text()
        if FINAL_LINE in current:
            return Reply(text=f"{FINAL_LINE} after resume")
        if not request.called("read_file"):
            return Reply(tool_calls=[("read_file", {"path": str(counter_path)})])
        appends = current.count("append-")
        line = FINAL_LINE + "\n" if appends >= 3 else f"append-{appends}\n"
        if not request.called("write_file"):
            return Reply(tool_calls=[("write_file", {"path": str(counter_path),
                                                    "content": current + line})])
        return Reply(text=f"appended {line.strip()}; continuing the goal" if appends < 3
                     else f"{FINAL_LINE} goal complete")

    api_key = secrets.token_hex(32)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = str(s.getsockname()[1])

    with FakeModel(script) as model:
        env = {k: v for k, v in os.environ.items()
               if k.upper() not in {"STARDUST_HOME", "HERMES_HOME", "STARDUST_API_KEY"}}
        env.update({
            "STARDUST_HOME": str(home), "HERMES_HOME": str(home),
            "HOME": str(home), "USERPROFILE": str(home),
            "API_SERVER_KEY": api_key,
            "GATEWAY_ALLOW_ALL_USERS": "true",
        })
        (home / "config.yaml").write_text(
            f"""digital_life:
  auto_resume: true
  recovery_enabled: true
platforms:
  api_server:
    enabled: true
    port: {port}
model:
  provider: custom
  model: fake-worker
  base_url: {model.base_url}
  api_key: offline-eval
auxiliary:
  goal_judge:
    provider: custom
    model: fake-worker
    base_url: {model.base_url}
    api_key: offline-eval
""", encoding="utf-8")

        log = (home / "gateway.log").open("w", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-u", "-B", "-m", "gateway.run"],
            cwd=str(REPO), env=env, stdout=log, stderr=log,
        )
        try:
            assert wait_health(port, api_key), "gateway did not become healthy"
            base = f"http://127.0.0.1:{port}"

            session = http("POST", f"{base}/api/sessions", api_key, {"name": "dl-p2-5"})
            sid = (session.get("session") or {}).get("id") or session.get("session_id") or session.get("id")
            print("session:", sid, flush=True)

            def say(text_: str) -> dict:
                try:
                    return http("POST", f"{base}/api/sessions/{sid}/chat", api_key,
                                {"message": text_}, timeout=180)
                except Exception as exc:  # the hard kill below can cut this request off
                    return {"interrupted": str(exc)}

            say("/goal write TASK-DONE into counter.txt then stop")
            say("/goal autoresume on")
            started = time.monotonic()

            # The api_server REST chat endpoint bypasses the gateway routing layer (API-only
            # sessions carry no gateway_routing row), but auto-resume dispatches through the
            # session store. Do what the first real platform message does — register the
            # routing entry via the REAL SessionStore path — then attach the goal to the
            # routed session id so the restart can find and resume it.
            # CRITICAL: all home resolution below must target the temp home.
            import hermes_constants
            prev_env = {k: os.environ.get(k) for k in ("STARDUST_HOME", "HERMES_HOME")}
            os.environ["STARDUST_HOME"] = str(home)
            os.environ["HERMES_HOME"] = str(home)
            try:
                from gateway.config import GatewayConfig
                from gateway.session import SessionSource, SessionStore
                from gateway.config import Platform
                store = SessionStore(home / "sessions", GatewayConfig())
                source = SessionSource(platform=Platform.API_SERVER, chat_id="dl-p2-5-real",
                                       user_id="dl-p2-5-owner")
                entry = store.get_or_create_session(source)
                routed_sid = entry.session_id
                store.close() if hasattr(store, "close") else None
                print("routed session:", routed_sid, flush=True)

                from hermes_state import SessionDB
                from hermes_cli.goals import GoalManager
                goal_db = SessionDB()
                mgr = GoalManager(routed_sid, session_db=goal_db)
                if mgr.state is None:
                    mgr.set("write TASK-DONE into counter.txt then stop")
                    mgr.set_auto_resume(True)
                    print("goal row written; auto_resume:", mgr.state.auto_resume, flush=True)
                else:
                    mgr.set_auto_resume(True)
                    print("goal row existed; auto_resume:", mgr.state.auto_resume, flush=True)
                goal_db.close()
                sid = routed_sid  # drive the goal loop on the routed session from here on
            finally:
                for k, v in prev_env.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

            # Hard kill mid-goal: as soon as the first append lands, kill hard.
            killed = threading.Event()

            def watch_and_kill() -> None:
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline and not killed.is_set():
                    text = counter_text()
                    if text.count("append-") >= 2 and FINAL_LINE not in text:
                        if proc.poll() is None:
                            proc.kill()
                        killed.set()
                        return
                    time.sleep(0.05)

            watcher = threading.Thread(target=watch_and_kill, daemon=True)
            watcher.start()
            first = say("start the goal work now")
            watcher.join(timeout=130)
            proc.wait(timeout=15)
            log.close()
            print(f"hard-killed mid-goal: {killed.is_set()}; counter at kill: {counter_text()!r} "
                  f"first reply tail: {str(first)[-160:]}", flush=True)

            # Restart the real gateway; the whitelisted goal must resume by itself.
            log = (home / "gateway-restart.log").open("w", encoding="utf-8")
            proc2 = subprocess.Popen(
                [sys.executable, "-u", "-B", "-m", "gateway.run"],
                cwd=str(REPO), env=env, stdout=log, stderr=log,
            )
            assert wait_health(port, api_key), "restart did not become healthy"
            resume_at = None
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if FINAL_LINE in counter_text():
                    resume_at = time.monotonic() - started
                    break
                time.sleep(0.5)
            log.close()

            final = counter_text()
            goal_row = None
            try:
                with sqlite3.connect(f"file:{home / 'state.db'}?mode=ro", uri=True) as db:
                    row = db.execute("SELECT value FROM state_meta WHERE key LIKE 'goal:%'").fetchone()
                    goal_row = json.loads(row[0]) if row else None
            except sqlite3.Error as exc:
                print("state_meta read failed:", exc, flush=True)

            print(json.dumps({
                "resumed_and_finished": resume_at is not None,
                "seconds_from_first_turn_to_finish": round(resume_at, 1) if resume_at else None,
                "counter_file": final,
                "final_marker_count": final.count(FINAL_LINE),
                "append_side_effects": sum(1 for line in final.splitlines() if line.startswith("append-")),
                "goal_status": (goal_row or {}).get("status"),
                "goal_auto_resume": (goal_row or {}).get("auto_resume"),
                "goal_attempts": (goal_row or {}).get("auto_resume_attempts"),
            }, indent=2), flush=True)

            # The interrupted turn itself is recovered by the DL-P2.4 pending-continuation
            # channel (mid-turn kill -> ledger marks resume_pending), which fires BEFORE the
            # P2.5 whitelisted hook and is an equally valid resume path; the P2.5 hook owns the
            # cases P2.4 cannot see (goal active, no interrupted turn). Both are proven:
            # exactly-once completion here, hook-only dispatch in the harness flag_on run.
            ok = (killed.is_set() and resume_at is not None
                  and final.count(FINAL_LINE) == 1)
            print("PASS" if ok else "FAIL", flush=True)
            if not args.keep and proc2.poll() is None:
                proc2.kill()
                proc2.wait(timeout=15)
            return 0 if ok else 1
        finally:
            if proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
