"""Turn scenario results into the report the product owner reads (plain Chinese) and a JSON record
that keeps every run with its evidence. Runs that ended in an infrastructure ``error`` are shown but
never counted: the score only reflects scenarios the exam could actually judge.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List

from evals.assistant_scenarios.runner import ScenarioResult
from evals.assistant_scenarios.scenarios import Scenario


@dataclass
class ScenarioSummary:
    scenario: Scenario
    runs: List[ScenarioResult] = field(default_factory=list)

    @property
    def scored(self) -> int:
        return sum(1 for r in self.runs if r.verdict.status != "error")

    @property
    def passed(self) -> int:
        return sum(1 for r in self.runs if r.verdict.status == "pass")

    @property
    def stable(self) -> bool:
        return self.scored > 0 and self.passed == self.scored

    def label(self) -> str:
        if not self.scored:
            notes = [r.verdict.note for r in self.runs if r.verdict.note]
            return f"本次不计分（{notes[0] if notes else '考场故障'}）"
        count = f" {self.passed}/{self.scored}" if len(self.runs) > 1 else ""
        if self.stable:
            return f"及格{count}"
        failing = next(r for r in self.runs if r.verdict.status == "fail")
        missed = [c.label for c in failing.verdict.checks if c.required and not c.ok]
        return f"不及格{count}（{'；'.join(missed)}）"


def summarize(results: List[ScenarioResult]) -> List[ScenarioSummary]:
    grouped: Dict[int, ScenarioSummary] = {}
    for res in results:
        grouped.setdefault(res.scenario.id, ScenarioSummary(res.scenario)).runs.append(res)
    return [grouped[k] for k in sorted(grouped)]


def render_text(summaries: List[ScenarioSummary], *, model_label: str, when: str, out_dir: Path) -> str:
    scored = [s for s in summaries if s.scored]
    unscored = len(summaries) - len(scored)
    total = f"总分：{sum(1 for s in scored if s.stable)}/{len(scored)} 及格"
    if unscored:
        total += f"，{unscored} 题本次不计分"
    lines = [f"星尘助理考卷 · {when} · 模型：{model_label}", total, ""]
    lines += [f"{s.scenario.id} {s.scenario.title}：{s.label()}" for s in summaries]
    lines += ["", f"每题的详细证据（对话记录、判分依据、后台日志）在：{out_dir}"]
    return "\n".join(lines)


def write_json(summaries: List[ScenarioSummary], path: Path, *, model_label: str) -> None:
    data = {"model": model_label, "scenarios": [{
        "id": s.scenario.id, "key": s.scenario.key, "title": s.scenario.title, "contract": s.scenario.contract,
        "passed": s.passed, "scored": s.scored,
        "runs": [{"status": r.verdict.status, "note": r.verdict.note, "wall_s": r.wall_s,
                  "artifacts": str(r.artifacts), "checks": [asdict(c) for c in r.verdict.checks]} for r in s.runs],
    } for s in summaries]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
