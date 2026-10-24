"""DL-P2.5 harness: whitelisted auto-resume of interrupted goals after restart.

Creates 10 interrupted goal sessions in a temp HOME (5 whitelisted, 3 plain, 2
paused), simulates a hard kill mid-step, then runs the real gateway startup
resume path and records what happened per session: resumed / executed /
duplicate side effects / time to resume.

Deterministic; no external model. Run from the repo root:

    python evals/digital_life/auto_resume.py <mode>

Modes:
    baseline   run against unmodified origin/main behaviour (no auto-resume)
    flag_off   run with digital_life.auto_resume=false (must equal baseline)
    flag_on    run with digital_life.auto_resume=true (5 whitelisted resume)
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

MODES = ("baseline", "flag_off", "flag_on")
WHITELISTED = [f"w{i}" for i in range(1, 6)]      # 5 whitelisted
PLAIN = [f"p{i}" for i in range(1, 4)]            # 3 not whitelisted
PAUSED = [f"h{i}" for i in range(1, 3)]           # 2 paused (also whitelisted)


def log(msg: str) -> None:
    print(msg, flush=True)


def run_mode(mode: str, home: Path, out: Path) -> dict:
    os.environ["STARDUST_HOME"] = str(home)
    os.environ["HERMES_HOME"] = str(home)
    os.environ["HOME"] = str(home)
    os.environ["USERPROFILE"] = str(home)

    # Fresh interpreter state per mode: the config loader caches per (path, signature),
    # and module-level singletons must not leak between modes.
    for name in list(sys.modules):
        if name.split(".")[0] in {"hermes_cli", "hermes_state", "agent", "gateway",
                                  "hermes_constants", "tui_gateway", "evals"}:
            del sys.modules[name]

    from hermes_state import SessionDB

    home.mkdir(parents=True, exist_ok=True)
    db = SessionDB()

    # ── Phase 1: create 10 interrupted goals ─────────────────────────────
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.session import SessionSource, SessionStore

    store_config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    store = SessionStore(home, store_config)
    from hermes_cli.goals import GoalManager

    sessions = {}
    sid_by_goal: dict[str, str] = {}
    for sid in WHITELISTED + PLAIN + PAUSED:
        source = SessionSource(platform=Platform.TELEGRAM, chat_id=sid,
                               chat_type="dm", user_id=f"u-{sid}")
        entry = store.get_or_create_session(source)
        real_id = entry.session_id
        sid_by_goal[sid] = real_id
        db.create_session(real_id, source="telegram", chat_id=sid)
        mgr = GoalManager(session_id=real_id, session_db=db)
        mgr.set(f"goal for {sid}: bump counter files")
        sessions[sid] = mgr
    for sid in WHITELISTED:
        mark = getattr(sessions[sid], "set_auto_resume", None)
        if mark is not None:
            mark(True)
    for sid in PAUSED:
        mark = getattr(sessions[sid], "set_auto_resume", None)
        if mark is not None:
            mark(True)
        sessions[sid]._state.status = "paused"
        sessions[sid]._save()

    # Journal mid-step crash for every session: the side-effecting step (write_file)
    # is COMMITTED — done steps never re-run — and the in-flight step at kill time is
    # a read (no side effect), so the journal gate classifies these ready.
    from agent import task_recovery

    side_effect_log = home / "side-effects.jsonl"
    for sid in WHITELISTED + PLAIN + PAUSED:
        real_id = sid_by_goal[sid]
        agent = type("A", (), {})()
        agent.session_id = real_id
        agent._session_db = db
        agent.identity = {"status": "ready", "individual_id": "identity-harness"}
        task_recovery.prepare(agent, f"goal for {sid}", task_id=f"task-{sid}")
        task_recovery.start_step(agent, "step-one", "write_file",
                                 {"path": "one", "content": "x"})
        task_recovery.commit_step(agent, "step-one", "done")
        task_recovery.start_step(agent, "step-two", "read_file", {"path": "two"})

    # ── Phase 2: "hard kill" = drop everything in-process, no cleanup ────
    goal_rows = {sid: json.loads(db.get_meta(f"goal:{sid_by_goal[sid]}")) for sid in
                 WHITELISTED + PLAIN + PAUSED}
    db.close()
    store.close() if hasattr(store, "close") else None

    # ── Phase 3: restart the startup resume path ─────────────────────────
    from collections import OrderedDict
    from unittest.mock import AsyncMock, MagicMock

    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.run import GatewayRunner
    from gateway.session import SessionStore

    started = time.perf_counter()

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    runner._running = True
    runner._shutdown_event = asyncio.Event()
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._pending_model_notes = {}
    runner._background_tasks = set()
    runner._draining = False
    runner._restart_requested = False
    runner._signal_initiated_shutdown = False
    runner._restart_task_started = False
    runner._restart_detached = False
    runner._restart_via_service = False
    runner._detached_restart_helper_started = False
    runner._restart_command_source = None
    runner._update_prompt_pending = {}
    runner._voice_mode = {}
    runner._session_model_overrides = {}
    runner._session_sources = OrderedDict()
    runner._session_sources_max = 512
    runner._shutdown_all_gateway_honcho = lambda: None
    runner._update_runtime_status = MagicMock()
    runner.session_store = SessionStore(home, runner.config)
    runner.delivery_router = MagicMock()
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()

    runner._session_states: dict = {}

    def _peek(key):
        return runner._session_states.get(key)

    def _state(key):
        # Minimal SessionState stand-in: the resume path only touches .turn.agent
        # and .turn.started_ts, then _persist_active_agents (stubbed below).
        state = runner._session_states.get(key)
        if state is None:
            turn = type("T", (), {})()
            turn.agent = None
            turn.started_ts = None
            state = type("S", (), {})()
            state.turn = turn
            runner._session_states[key] = state
        return state

    runner._peek_session_state = _peek
    runner._session_state = _state
    runner._release_running_agent_state = lambda key: (
        runner._session_states.pop(key, None) and None
    ) or setattr(_state(key).turn, "agent", None)
    runner._persist_active_agents = lambda: None
    runner._is_user_authorized_for_source = lambda source: True

    dispatched: list[tuple[str, str]] = []
    recorded_prompts: dict[str, str] = {}

    class HarnessAdapter:
        platform = Platform.TELEGRAM

        async def handle_message(self, event):
            sid = getattr(event.source, "chat_id", None)
            dispatched.append((sid, event.text))
            recorded_prompts[sid] = event.text

        async def send(self, *a, **kw):
            return None

    runner.adapters = {Platform.TELEGRAM: HarnessAdapter()}
    runner._adapter_for_source = (
        type(runner)._adapter_for_source.__get__(runner, type(runner))
        if hasattr(type(runner), "_adapter_for_source") else
        (lambda source: runner.adapters.get(getattr(source, "platform", None)))
    )

    for name in ("_session_key_for_source", "_retain_background_task"):
        fn = getattr(type(runner), name, None)
        if fn is not None:
            setattr(runner, name, fn.__get__(runner, type(runner)))

    runner._session_entry_for_id = (
        type(runner)._session_entry_for_id.__get__(runner, type(runner))
        if hasattr(type(runner), "_session_entry_for_id") else None
    )
    runner._is_session_running = (
        type(runner)._is_session_running.__get__(runner, type(runner))
        if hasattr(type(runner), "_is_session_running") else (lambda key: False)
    )

    async def amain() -> int:
        hook = getattr(runner, "_schedule_whitelisted_auto_resume", None)
        if hook is None:
            return 0
        result = hook()
        if asyncio.iscoroutine(result):
            result = await result
        # Never two owners for one task: a SECOND invocation while the first
        # dispatches are still in flight must not double-dispatch any session
        # (slot claim holds the sentinel until the resume turn completes).
        second = hook()
        if asyncio.iscoroutine(second):
            second = await second
        for _ in range(50):
            await asyncio.sleep(0.02)
            if len(dispatched) >= int(result) + int(second):
                break
        return int(result)

    scheduled = asyncio.run(amain())
    elapsed = time.perf_counter() - started

    # Duplicate-dispatch assertion (two owners would double-dispatch a session).
    double_owners = [sid for sid in {d[0] for d in dispatched}
                     if sum(1 for d in dispatched if d[0] == sid) > 1]
    if double_owners:
        raise SystemExit(f"HARNESS FAIL: sessions dispatched twice: {double_owners}")

    # ── Phase 4: record what happened ────────────────────────────────────
    db2 = SessionDB()
    results = []
    for sid in WHITELISTED + PLAIN + PAUSED:
        before = goal_rows[sid]
        after = json.loads(db2.get_meta(f"goal:{sid_by_goal[sid]}") or "{}")
        results.append({
            "session": sid,
            "kind": "whitelisted" if sid in WHITELISTED else ("paused" if sid in PAUSED else "plain"),
            "resumed": any(d[0] == sid for d in dispatched),
            "prompt": recorded_prompts.get(sid, ""),
            "attempts_before": before.get("auto_resume_attempts", 0),
            "attempts_after": after.get("auto_resume_attempts", 0),
            "status_before": before.get("status"),
            "status_after": after.get("status"),
        })
    db2.close()

    summary = {
        "mode": mode,
        "scheduled": scheduled,
        "dispatch_count": len(dispatched),
        "elapsed_s": round(elapsed, 3),
        "resumed_whitelisted": sum(1 for r in results if r["kind"] == "whitelisted" and r["resumed"]),
        "resumed_plain": sum(1 for r in results if r["kind"] == "plain" and r["resumed"]),
        "resumed_paused": sum(1 for r in results if r["kind"] == "paused" and r["resumed"]),
        "results": results,
    }
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("--home", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    base = Path(args.home) if args.home else REPO / "evals" / "digital_life" / ".auto_resume_tmp"
    home = base / args.mode
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True)

    config = home / "config.yaml"
    if args.mode == "flag_on":
        config.write_text("digital_life:\n  auto_resume: true\n  recovery_enabled: true\n",
                          encoding="utf-8")
    elif args.mode == "flag_off":
        config.write_text("digital_life:\n  auto_resume: false\n  recovery_enabled: true\n",
                          encoding="utf-8")
    else:
        config.write_text("digital_life:\n  recovery_enabled: true\n", encoding="utf-8")

    out = Path(args.out) if args.out else base / f"{args.mode}.json"
    summary = run_mode(args.mode, home, out)
    log(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))
    for r in summary["results"]:
        log(f"  {r['kind']:<11} {r['session']}: resumed={r['resumed']} "
            f"attempts {r['attempts_before']}->{r['attempts_after']} "
            f"status {r['status_before']}->{r['status_after']}")
    ok = (summary["resumed_whitelisted"] == 5 and summary["resumed_plain"] == 0
          and summary["resumed_paused"] == 0)
    log(f"PASS={ok}")
    return 0 if ok or args.mode != "flag_on" else 1


if __name__ == "__main__":
    raise SystemExit(main())
