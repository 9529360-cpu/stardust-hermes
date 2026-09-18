"""Continuous backend validation and status dashboard for the dedicated backend workstation."""
from __future__ import annotations

import argparse
import html
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
ARTIFACTS = Path(os.environ.get("STARDUST_BACKEND_LAB_ARTIFACTS", r"D:\\远程工作区\\artifacts\\backend-lab"))
GIT = Path(os.environ.get("STARDUST_BACKEND_LAB_GIT", r"D:\\DevTools\\Git\\cmd\\git.exe"))
BASH = Path(os.environ.get("STARDUST_BACKEND_LAB_BASH", r"D:\\DevTools\\Git\\bin\\bash.exe"))
NODE = Path(os.environ.get("STARDUST_BACKEND_LAB_NODE", r"D:\\DevTools\\Node26\\node.exe"))
ALLURE_JS = Path(os.environ.get(
    "STARDUST_BACKEND_LAB_ALLURE_JS", r"D:\\DevTools\\Allure3\\node_modules\\allure\\cli.js"
))
ALLURE_RESULTS = ARTIFACTS / "allure-results"
ALLURE_LATEST = ARTIFACTS / "allure" / "latest"
ALLURE_HISTORY = ARTIFACTS / "allure" / "history.jsonl"
ALLURE_CONFIG = ARTIFACTS / "allure" / "allurerc.json"
CURRENT_RUN = ARTIFACTS / "current.json"
BACKEND_PREFIXES = ("agent/", "cron/", "gateway/", "hermes_cli/", "plugins/", "tools/", "tui_gateway/",
                    "tests/agent/", "tests/gateway/", "tests/hermes_cli/", "tests/plugins/",
                    "tests/tools/", "tests/tui_gateway/")
BACKEND_ROOT_FILES = {"SOUL.md", "run_agent.py", "hermes_state_messages.py", "hermes_state_rewind.py"}
FAST_TESTS = [
    "tests/tui_gateway/test_compute_host_phase1.py",
    "tests/tui_gateway/test_compute_host_late_compress_ack.py",
    "tests/tui_gateway/test_compute_host_turn_protocol.py",
    "tests/agent/test_prompt_builder.py",
    "tests/tools/test_memory_tool.py",
    "tests/tools/test_todo_tool.py",
    "tests/tui_gateway/test_todo_state_events.py",
]
DEEP_PACKS: list[tuple[str, list[str]]] = [
    ("hosted-room", [
        "tests/tui_gateway/test_failed_turn_retention.py",
        "tests/tui_gateway/test_hosted_room_driver_runtime.py",
        "tests/tui_gateway/test_hosted_room_peer_transport.py",
        "tests/tui_gateway/test_hosted_room_server_rpc.py",
        "tests/tui_gateway/test_hosted_room_service.py",
        "tests/tui_gateway/test_auto_continue.py",
    ]),
    ("timeline", [
        "tests/tui_gateway/test_todo_state_events.py",
        "tests/tui_gateway/test_goal_command.py",
        "tests/tui_gateway/test_loop_command.py",
        "tests/tui_gateway/test_serve_exit_flush.py",
        "tests/tui_gateway/test_isolated_orphan_activity.py",
        "tests/tui_gateway/test_gui_surface_toolsets.py",
        "tests/tui_gateway/test_stranded_session_adoption.py",
    ]),
    ("agent-core", [
        "tests/agent/test_coding_context.py",
        "tests/agent/test_compression_persistence.py",
        "tests/agent/test_compression_rotation_state.py",
        "tests/agent/test_concurrent_interrupt.py",
        "tests/agent/test_prompt_builder.py",
        "tests/agent/test_run_agent.py",
        "tests/agent/test_sequential_tool_timeout.py",
        "tests/agent/test_system_prompt.py",
        "tests/agent/test_system_prompt_restore.py",
        "tests/agent/test_tool_batch_segmentation.py",
        "tests/agent/test_tool_call_incremental_persistence.py",
        "tests/agent/test_turn_context.py",
        "tests/tools/test_memory_tool.py",
        "tests/tools/test_todo_tool.py",
    ]),
]

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def git(*args: str) -> str:
    proc = subprocess.run([str(GIT), "-C", str(REPO), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False)
    return proc.stdout.strip()

def repo_snapshot() -> dict[str, Any]:
    return {"branch": git("branch", "--show-current"), "head": git("rev-parse", "HEAD"),
            "status": git("status", "--short")}

def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # Windows readers can briefly hold the destination without delete sharing.
            if os.name != "nt":
                raise
            if attempt < 19:
                time.sleep(min(0.25, 0.01 * (attempt + 1)))
    # A long-lived Windows reader can deny rename/delete sharing indefinitely while
    # still allowing writes. Keep atomic replace as the normal path; only after the
    # bounded retry budget use a flushed in-place publish rather than lose the ledger.
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    tmp.unlink(missing_ok=True)

def bash_path(path: Path) -> str:
    drive = path.drive.rstrip(":").lower()
    return f"/{drive}{path.as_posix()[2:]}"

def bash_repo_path() -> str:
    return bash_path(REPO)

def prepare_allure_results(profile: str, pack: str) -> None:
    ALLURE_RESULTS.mkdir(parents=True, exist_ok=True)
    for child in ALLURE_RESULTS.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    ALLURE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(ALLURE_CONFIG, {
        "name": "Stardust Backend Lab",
        "historyPath": str(ALLURE_HISTORY).replace("\\", "/"),
        "appendHistory": True,
        "historyLimit": 200,
    })
    snapshot = repo_snapshot()
    (ALLURE_RESULTS / "environment.properties").write_text(
        "\n".join([
            f"profile={profile}",
            f"pack={pack}",
            f"branch={snapshot['branch']}",
            f"head={snapshot['head']}",
            f"machine={os.environ.get('COMPUTERNAME', '')}",
        ]) + "\n",
        encoding="utf-8",
    )

def test_command(paths: list[str]) -> list[str]:
    tests = " ".join(shlex.quote(path) for path in paths)
    allure_results = shlex.quote(bash_path(ALLURE_RESULTS))
    return [str(BASH), "-lc",
            f"cd {shlex.quote(bash_repo_path())} && ./scripts/run_tests.sh {tests} -q --alluredir={allure_results}"]

def changed_backend_python() -> list[str]:
    tracked = git("diff", "--name-only", "--diff-filter=ACMR", "HEAD", "--", "*.py").splitlines()
    untracked = git("ls-files", "--others", "--exclude-standard", "--", "*.py").splitlines()
    names = {name.replace("\\", "/") for name in tracked + untracked if name.strip()}
    return sorted(name for name in names if name in BACKEND_ROOT_FILES or name.startswith(BACKEND_PREFIXES))

def tail(path: Path, lines: int = 80) -> str:
    if not path.exists():
        return ""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return "".join(deque(handle, maxlen=lines))

def write_current(profile: str, pack: str, state: str, *, stage: str = "", stage_started_at: str = "",
                  process_pid: int | None = None) -> None:
    atomic_json(CURRENT_RUN, {
        "profile": profile,
        "pack": pack,
        "state": state,
        "stage": stage,
        "stage_started_at": stage_started_at,
        "heartbeat_at": utc_now(),
        "controller_pid": os.getpid(),
        "process_pid": process_pid,
        "repository": repo_snapshot(),
    })

def run_stage(
    label: str,
    argv: list[str],
    run_dir: Path,
    timeout: int,
    *,
    profile: str,
    pack: str,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    log_path = run_dir / f"{label}.log"
    started = time.monotonic()
    stage_started_at = utc_now()
    timed_out = False
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        proc = subprocess.Popen(
            argv,
            cwd=REPO,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=creationflags,
        )
        deadline = started + timeout
        write_current(profile, pack, "running", stage=label, stage_started_at=stage_started_at,
                      process_pid=proc.pid)
        while True:
            polled = proc.poll()
            if polled is not None:
                code = int(polled)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                code = 124
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                else:
                    proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                log.write("\nBACKEND LAB: command timed out; process tree terminated\n")
                break
            write_current(profile, pack, "running", stage=label, stage_started_at=stage_started_at,
                          process_pid=proc.pid)
            time.sleep(2)
    write_current(profile, pack, "stage-complete", stage=label, stage_started_at=stage_started_at)
    return {"label": label, "argv": argv, "exit_code": code, "timed_out": timed_out,
            "duration_seconds": round(time.monotonic() - started, 2),
            "log": str(log_path), "tail": tail(log_path)}

def allure_generate_command(report_dir: Path, profile: str, pack: str) -> list[str]:
    return [
        str(NODE),
        str(ALLURE_JS),
        "generate",
        str(ALLURE_RESULTS),
        "--config",
        str(ALLURE_CONFIG),
        "--output",
        str(report_dir),
        "--report-name",
        f"Stardust Backend Lab · {profile}/{pack}",
        "--history-limit",
        "200",
    ]

def publish_allure(report_dir: Path, profile: str, pack: str) -> None:
    if not report_dir.exists():
        return
    pack_dir = ARTIFACTS / "allure" / f"{profile}-{pack}"
    for target in (pack_dir, ALLURE_LATEST):
        shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(report_dir, target)

def render_html(payload: dict[str, Any]) -> str:
    rows = []
    for stage in payload.get("stages", []):
        ok = stage.get("exit_code") == 0
        cls = "ok" if ok else "bad"
        tail_text = html.escape(stage.get("tail", ""))
        rows.append(
            f"<section class=\"card {cls}\"><h2>{html.escape(stage.get('label', ''))}</h2>"
            f"<p>exit={stage.get('exit_code')} · {stage.get('duration_seconds')}s</p>"
            f"<pre>{tail_text}</pre></section>"
        )
    repo = payload.get("repository", {})
    overall_ok = payload.get("overall_ok", payload.get("ok"))
    status = "GREEN" if overall_ok else "RED"
    color = "#0a7f35" if overall_ok else "#b42318"
    unresolved = payload.get("open_failures", [])
    unresolved_html = "".join(
        f"<li><b>{html.escape(str(item.get('profile')))} / {html.escape(str(item.get('pack')))}</b>"
        f" · {html.escape(str(item.get('finished_at')))}"
        f" · <a href=\"allure/{html.escape(str(item.get('profile')))}-{html.escape(str(item.get('pack')))}/index.html\">test evidence</a></li>"
        for item in unresolved
    )
    return f"""<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="4">
    <title>Stardust Backend Lab</title><style>
    body{{font-family:Segoe UI,Arial;margin:24px;background:#f6f7f9;color:#111}}
    .head{{display:flex;gap:18px;align-items:center}} .badge{{font-weight:700;color:{color}}}
    .card{{background:white;border-left:6px solid #bbb;padding:14px;margin:12px 0}}
    .ok{{border-color:#0a7f35}} .bad{{border-color:#b42318}}
    pre{{white-space:pre-wrap;max-height:360px;overflow:auto;background:#111;color:#eee;padding:12px}}
    </style><div class="head"><h1>Stardust Backend Lab</h1><span class="badge">{status}</span></div>
    <section id="live" class="card">Live: loading current stage…</section>
    <p>profile={html.escape(str(payload.get("profile")))} · pack={html.escape(str(payload.get("pack")))}</p>
    <p>branch={html.escape(str(repo.get("branch")))} · head={html.escape(str(repo.get("head"))[:12])}</p>
    <p><a href="http://127.0.0.1:8766" target="_blank">Open live Allure</a> · <a href="allure/latest/index.html">Open latest completed Allure evidence</a></p>
    <h2>Open failures</h2><ul>{unresolved_html or "<li>none</li>"}</ul>
    {"".join(rows)}
    <script>
    async function refreshLive() {{
      const el = document.getElementById("live");
      try {{
        const r = await fetch("current.json?t=" + Date.now(), {{cache:"no-store"}});
        const d = await r.json();
        const hb = Date.parse(d.heartbeat_at || "");
        const started = Date.parse(d.stage_started_at || "");
        const age = Number.isFinite(hb) ? Math.max(0, (Date.now() - hb) / 1000) : 9999;
        const elapsed = Number.isFinite(started) ? Math.max(0, (Date.now() - started) / 1000) : 0;
        const stale = d.state === "running" && age > 8;
        const liveState = stale ? "STALE / POSSIBLE HANG" : (d.state || "unknown").toUpperCase();
        el.className = "card " + (stale ? "bad" : (d.state === "running" ? "ok" : ""));
        el.textContent = `Live: ${{liveState}} · ${{d.profile || ""}}/${{d.pack || ""}} · stage=${{d.stage || "-"}} · elapsed=${{elapsed.toFixed(1)}}s · heartbeatAge=${{age.toFixed(1)}}s`;
      }} catch (e) {{
        el.className = "card bad";
        el.textContent = "Live: status feed unavailable";
      }}
    }}
    refreshLive(); setInterval(refreshLive, 2000);
    </script>"""

def next_pack() -> tuple[str, list[str]]:
    path = ARTIFACTS / "cycle.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        index = int(state.get("next_index", 0)) % len(DEEP_PACKS)
    except Exception:
        index = 0
    atomic_json(path, {"next_index": (index + 1) % len(DEEP_PACKS), "updated_at": utc_now()})
    return DEEP_PACKS[index]

def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock() -> int | None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS / "run.lock"
    if path.exists():
        try:
            owner_pid = int(path.read_text(encoding="utf-8").split()[0])
        except Exception:
            owner_pid = 0
        if not pid_alive(owner_pid) or time.time() - path.stat().st_mtime > 6 * 3600:
            path.unlink(missing_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    os.write(fd, f"{os.getpid()} {utc_now()}".encode("utf-8"))
    return fd

def release_lock(fd: int | None) -> None:
    if fd is not None:
        os.close(fd)
        (ARTIFACTS / "run.lock").unlink(missing_ok=True)

def _latest_durable_payload() -> dict[str, Any]:
    """Return the newest successfully decoded per-pack result."""
    candidates: list[dict[str, Any]] = []
    for path in ARTIFACTS.glob("latest-*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if item.get("finished_at"):
            candidates.append(item)
    if not candidates:
        raise RuntimeError("backend-lab has no durable per-pack result to recover")
    return max(candidates, key=lambda item: str(item.get("finished_at") or ""))


def _publish_aggregate(payload: dict[str, Any]) -> None:
    """Rebuild the aggregate ledger from durable per-pack results."""
    failures: list[dict[str, Any]] = []
    for path in sorted(ARTIFACTS.glob("latest-*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if item.get("ok") is False:
            failures.append({
                "profile": item.get("profile"),
                "pack": item.get("pack"),
                "finished_at": item.get("finished_at"),
                "repository": item.get("repository", {}),
                "failed_stages": [
                    stage for stage in item.get("stages", [])
                    if stage.get("exit_code") != 0
                ],
            })

    aggregate = {**payload, "overall_ok": not failures, "open_failures": failures}
    atomic_json(ARTIFACTS / "latest.json", aggregate)
    (ARTIFACTS / "latest.html").write_text(render_html(aggregate), encoding="utf-8")
    if failures:
        atomic_json(ARTIFACTS / "needs_attention.json", {
            "updated_at": utc_now(),
            "failures": failures,
        })
    else:
        (ARTIFACTS / "needs_attention.json").unlink(missing_ok=True)
    profile = str(payload.get("profile") or "")
    pack = str(payload.get("pack") or "")
    history = ARTIFACTS / "history"
    history.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    atomic_json(history / f"{stamp}-{profile}-{pack}.json", aggregate)
    write_current(profile, pack, "idle")

def record(profile: str, pack: str, stages: list[dict[str, Any]]) -> int:
    payload = {
        "profile": profile, "pack": pack, "finished_at": utc_now(),
        "ok": all(stage["exit_code"] == 0 for stage in stages),
        "repository": repo_snapshot(), "stages": stages,
    }
    # Per-pack evidence is the durable source used to rebuild the aggregate ledger.
    atomic_json(ARTIFACTS / f"latest-{profile}-{pack}.json", payload)
    _publish_aggregate(payload)
    return 0 if payload["ok"] else 1

def build_stages(profile: str) -> tuple[str, list[tuple[str, list[str], int]]]:
    changed = changed_backend_python()
    lint = [sys.executable, "-m", "ruff", "check", *changed] if changed else [sys.executable, "-m", "ruff", "--version"]
    if profile == "fast":
        return "compute-host", [("ruff", lint, 300), ("tests", test_command(FAST_TESTS), 900),
                                ("diff-check", [str(GIT), "-C", str(REPO), "diff", "--check"], 120)]
    pack, tests = next_pack()
    # agent-core contains test_run_agent.py, whose per-file runner wall time can exceed
    # four minutes on the Windows backend even when pytest itself is healthy. Keep the
    # other deep packs' tighter hang detector, but give agent-core enough budget to
    # surface a real assertion failure instead of replacing it with timeout evidence.
    test_timeout = 480 if pack == "agent-core" else 240
    return pack, [("ruff", lint, 600), ("tests", test_command(tests), test_timeout),
                  ("diff-check", [str(GIT), "-C", str(REPO), "diff", "--check"], 120)]

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", choices=("fast", "deep"))
    args = parser.parse_args()
    fd = acquire_lock()
    if fd is None:
        print("backend-lab: another run is active; skipping")
        return 0
    try:
        snapshot = repo_snapshot()
        if snapshot["branch"] != "dev/stardust-backend-runtime":
            return record(args.profile, "branch-guard", [{
                "label": "branch-guard", "argv": [], "exit_code": 2, "timed_out": False,
                "duration_seconds": 0.0, "log": "",
                "tail": f"Expected dev/stardust-backend-runtime, found {snapshot['branch']}",
            }])
        pack, specs = build_stages(args.profile)
        run_dir = ARTIFACTS / "runs" / datetime.now().strftime(f"%Y%m%d-%H%M%S-{args.profile}-{pack}")
        run_dir.mkdir(parents=True, exist_ok=True)
        prepare_allure_results(args.profile, pack)
        stages = []
        tests_ran = False
        for label, argv, timeout in specs:
            if label == "tests":
                tests_ran = True
            stages.append(run_stage(label, argv, run_dir, timeout, profile=args.profile, pack=pack))
            if stages[-1]["exit_code"] != 0:
                break
        if tests_ran:
            report_dir = run_dir / "allure-report"
            shutil.rmtree(report_dir, ignore_errors=True)
            allure_stage = run_stage(
                "allure",
                allure_generate_command(report_dir, args.profile, pack),
                run_dir,
                120,
                profile=args.profile,
                pack=pack,
            )
            stages.append(allure_stage)
            if allure_stage["exit_code"] == 0:
                publish_allure(report_dir, args.profile, pack)
        return record(args.profile, pack, stages)
    finally:
        release_lock(fd)

if __name__ == "__main__":
    raise SystemExit(main())
