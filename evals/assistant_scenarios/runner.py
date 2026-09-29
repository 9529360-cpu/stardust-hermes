"""Run one scenario end to end: sandbox → backend → conversation → settle (or restart) → observe → grade.

Only infrastructure failures (backend never starts, the wire breaks) make a verdict ``error``. A model
that stalls, never answers, or never reports back is graded on what actually happened, so slowness
and silence show up as failed checks with evidence, not as missing results.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from evals.assistant_scenarios import fixtures, sandbox
from evals.assistant_scenarios.gateway import GatewayError, GatewayProcess
from evals.assistant_scenarios.grading import Observation, RestartFacts, Verdict
from evals.assistant_scenarios.scenarios import Scenario
from evals.assistant_scenarios.trace import Trace

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_STATES = frozenset({"running", "stalling", "finalizing"})
UNDELIVERED = frozenset({"pending", "claimed"})


@dataclass
class RunConfig:
    endpoint: sandbox.ModelEndpoint
    out_dir: Path
    python: str = sys.executable
    repo_root: Path = REPO_ROOT
    tool_bin: Optional[Path] = None
    home_extra: Dict[str, Any] = field(default_factory=dict)
    work_root: Optional[Path] = None
    turn_timeout: float = 300.0
    settle_timeout: float = 900.0
    quiet_seconds: float = 12.0
    restart_reply_timeout: float = 300.0
    keep: bool = False


@dataclass
class ScenarioResult:
    scenario: Scenario
    verdict: Verdict
    trace: Trace
    wall_s: float
    artifacts: Path
    notes: List[str] = field(default_factory=list)


def wait_until_settled(trace: Trace, read_ledger: Callable[[], List[Dict[str, Any]]], *, timeout: float,
                       quiet: float, poll: float = 1.0) -> bool:
    """True once nothing is in flight — no open turn, no live or undelivered background result — and
    neither the event stream nor the delegation ledger changed for ``quiet`` seconds. The ledger counts
    as activity because the backend records a delivery just before the turn that delivers it starts."""
    deadline = time.monotonic() + timeout
    signature: Any = None
    changed_at = time.monotonic()
    while time.monotonic() < deadline:
        rows = read_ledger()
        now = time.monotonic()
        current = tuple((r["delegation_id"], r["state"], r["delivery_state"]) for r in rows)
        if current != signature:
            signature, changed_at = current, now
        pending = trace.busy or any(r["state"] in LIVE_STATES or r["delivery_state"] in UNDELIVERED for r in rows)
        if not pending and now - max(trace.last_event_at or 0.0, changed_at) >= quiet:
            return True
        time.sleep(poll)
    return False


class RequestPolicy:
    """The simulated user answering the backend's questions: approvals get the scenario's choice,
    clarify gets the scenario's scripted answer, and secrets/sudo are never handed out."""

    def __init__(self, scenario: Scenario, trace: Trace):
        self.scenario, self.trace = scenario, trace

    def __call__(self, method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        now = time.monotonic()
        if method == "approval":
            self.trace.record_approval(params, choice=self.scenario.approval_choice, at=now)
            return {"choice": self.scenario.approval_choice}
        if method == "clarify":
            answer = self.scenario.clarify_answer
            self.trace.record_clarify(params, answer=answer, at=now)
            questions = params.get("questions") or []
            return {"answers": {q.get("qid"): answer for q in questions}} if questions else {"answer": answer}
        return None


@dataclass
class _Run:
    scenario: Scenario
    cfg: RunConfig
    root: Path
    trace: Trace = field(default_factory=Trace)
    notes: List[str] = field(default_factory=list)
    gateways: List[GatewayProcess] = field(default_factory=list)
    sid: str = ""
    stored_id: str = ""

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def workspace(self) -> Path:
        return self.root / "ws"

    @property
    def gateway(self) -> GatewayProcess:
        return self.gateways[-1]

    def start_gateway(self) -> GatewayProcess:
        env = sandbox.child_env(os.environ, home=self.home, repo_root=self.cfg.repo_root,
                                api_key=self.cfg.endpoint.api_key, tool_bin=self.cfg.tool_bin,
                                profile=self.root / "profile")
        gateway = GatewayProcess(python=self.cfg.python, repo_root=self.cfg.repo_root, env=env,
                                 log_path=self.root / f"gateway-{len(self.gateways) + 1}.log",
                                 on_event=self.trace.ingest, on_request=RequestPolicy(self.scenario, self.trace))
        self.gateways.append(gateway)
        gateway.start()
        return gateway

    def submit(self, text: str) -> bool:
        self.trace.mark_submit(self.sid, text, at=time.monotonic())
        self.gateway.call("prompt.submit", {"session_id": self.sid, "text": text})
        done = self.gateway.wait_until(lambda: not self.trace.busy, self.cfg.turn_timeout)
        if not done:
            self.notes.append(f"一轮对话 {self.cfg.turn_timeout:.0f} 秒内没有结束")
        return done

    def converse(self) -> None:
        if not self.submit(self.scenario.ask):
            return
        for reply in self.scenario.followups:
            last = self.trace.turns[-1].text.strip() if self.trace.turns else ""
            if not last.endswith(("？", "?")) or not self.submit(reply):
                return

    def settle(self) -> None:
        if not wait_until_settled(self.trace, lambda: sandbox.read_delegations(self.home),
                                  timeout=self.cfg.settle_timeout, quiet=self.cfg.quiet_seconds):
            self.notes.append(f"等后台结果 {self.cfg.settle_timeout:.0f} 秒仍未结束")

    def restart(self) -> RestartFacts:
        """Kill the backend while the background task runs, start a new one on the same home, reopen the
        conversation, and record what the runtime and the conversation say about the lost work."""
        self.gateway.wait_until(lambda: sandbox.read_delegations(self.home), 60)
        rows = sandbox.read_delegations(self.home)
        if not rows:
            return RestartFacts(None, None, self._seeded_job(), time.monotonic())
        target = rows[-1]
        if target["state"] not in LIVE_STATES:
            return RestartFacts(target["state"], target["state"], self._seeded_job(), time.monotonic())
        self.gateway.kill()
        restart_at = time.monotonic()
        resumed = self.start_gateway().call(
            "session.resume", {"session_id": self.stored_id, "cols": 96, "source": "desktop"})
        self.trace.watch(resumed["session_id"])
        self.gateway.wait_until(lambda: any(t.started_at >= restart_at and t.completed_at is not None
                                            for t in self.trace.turns), self.cfg.restart_reply_timeout)
        self.settle()
        after = next((r for r in sandbox.read_delegations(self.home)
                      if r["delegation_id"] == target["delegation_id"]), None)
        return RestartFacts(target["state"], after["state"] if after else None, self._seeded_job(), restart_at)

    def _seeded_job(self) -> Optional[Dict[str, Any]]:
        name = (self.scenario.seed_job or {}).get("name")
        return next((j for j in sandbox.read_cron_jobs(self.home) if name and j.get("name") == name), None)

    def close(self) -> None:
        for gateway in self.gateways:
            gateway.close()


def _sandbox_root(scenario: Scenario, cfg: RunConfig) -> Path:
    if cfg.work_root is None:
        return Path(tempfile.mkdtemp(prefix=f"stardust-exam-{scenario.id:02d}-"))
    root = cfg.work_root / f"{scenario.id:02d}-{scenario.key}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def run_scenario(scenario: Scenario, cfg: RunConfig) -> ScenarioResult:
    started = time.monotonic()
    run = _Run(scenario, cfg, _sandbox_root(scenario, cfg))
    scenario.build(run.workspace)
    sandbox.write_home_config(run.home, cfg.endpoint, cfg.home_extra)
    first_commit = fixtures.first_commit(run.workspace) if scenario.check_git else ""
    before = sandbox.snapshot(run.workspace)
    facts: Optional[RestartFacts] = None
    try:
        created = run.start_gateway().call(
            "session.create", {"cols": 96, "source": "desktop", "cwd": str(run.workspace), "fast": False})
        run.sid, run.stored_id = created["session_id"], created.get("stored_session_id") or created["session_id"]
        run.trace.watch(run.sid)
        if scenario.seed_job:
            run.gateway.call("cron.manage", {"action": "add", "deliver": "local", **scenario.seed_job})
        run.converse()
        if scenario.restart:
            facts = run.restart()
        else:
            run.settle()
        run.close()  # observe a quiescent sandbox: nothing may still be writing
        obs = Observation(
            trace=run.trace, files_before=before, files_after=sandbox.snapshot(run.workspace),
            cron_jobs=sandbox.read_cron_jobs(run.home), delegations=sandbox.read_delegations(run.home),
            test_run=sandbox.run_unit_tests(run.workspace, cfg.python) if scenario.run_tests else None,
            git_intact=fixtures.history_intact(run.workspace, first_commit) if scenario.check_git else None,
            restart=facts)
    except (GatewayError, OSError, KeyError, ValueError, sqlite3.Error) as exc:
        run.close()
        verdict = Verdict([], status="error", note=f"考场故障：{exc}")
        return _finish(run, verdict, started, traceback.format_exc())
    return _finish(run, scenario.grade(obs), started, "")


def _finish(run: _Run, verdict: Verdict, started: float, error: str) -> ScenarioResult:
    out = run.cfg.out_dir / f"{run.scenario.id:02d}-{run.scenario.key}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "trace.json").write_text(json.dumps(run.trace.to_json(), ensure_ascii=False, indent=1, default=str),
                                    encoding="utf-8")
    (out / "verdict.json").write_text(json.dumps(
        {"scenario": run.scenario.key, "status": verdict.status, "note": verdict.note, "notes": run.notes,
         "checks": [asdict(c) for c in verdict.checks]}, ensure_ascii=False, indent=1), encoding="utf-8")
    for log in run.root.glob("gateway-*.log"):
        shutil.copy2(log, out / log.name)
    if error:
        (out / "error.txt").write_text(error, encoding="utf-8")
    if verdict.status == "error" and not verdict.note:
        verdict.note = "考场故障"
    if run.notes:
        verdict.note = "；".join(filter(None, [verdict.note, *run.notes]))
    if not run.cfg.keep and run.cfg.work_root is None:
        shutil.rmtree(run.root, ignore_errors=True)
    return ScenarioResult(run.scenario, verdict, run.trace, round(time.monotonic() - started, 1), out, run.notes)
