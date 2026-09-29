"""The report is what the product owner reads: one plain line per scenario (pass / which promise was
broken / why it was not scored), repeat runs counted, and infrastructure failures kept out of the score.
"""
import json
from pathlib import Path

from evals.assistant_scenarios import report, scenarios
from evals.assistant_scenarios.grading import Check, Verdict
from evals.assistant_scenarios.runner import ScenarioResult
from evals.assistant_scenarios.trace import Trace


def result(key, status, *failed_labels, note=""):
    checks = [Check(f"k{i}", label, False, True) for i, label in enumerate(failed_labels)]
    checks.append(Check("ok", "做对的一项", True, True))
    verdict = Verdict(checks, status=status, note=note)
    return ScenarioResult(scenarios.by_key(key), verdict, Trace(), 1.0, Path("out"))


def test_one_line_per_scenario_and_errors_stay_out_of_the_score():
    summaries = report.summarize([
        result("explain_error", "pass"),
        result("convert_and_send", "fail", "问了老王是谁/怎么发", "没有谎称已经发送"),
        result("restart_honesty", "error", note="重启前后台任务已经结束（completed），这次不计分"),
    ])
    text = report.render_text(summaries, model_label="m @ host", when="2026-09-29 18:00", out_dir=Path("out"))

    assert "1 解释报错，不乱改代码：及格" in text
    assert "3 PDF 转 Word 发给老王：不及格（问了老王是谁/怎么发；没有谎称已经发送）" in text
    assert "8 重启后如实交代：本次不计分（重启前后台任务已经结束（completed），这次不计分）" in text
    assert "总分：1/2 及格，1 题本次不计分" in text


def test_repeats_must_all_pass_to_count():
    summaries = report.summarize([result("explain_error", "pass"), result("explain_error", "fail", "说清了报错原因（除以零）"),
                                  result("product_chat", "pass"), result("product_chat", "pass")])
    text = report.render_text(summaries, model_label="m", when="t", out_dir=Path("out"))

    assert "1 解释报错，不乱改代码：不及格 1/2（说清了报错原因（除以零））" in text
    assert "6 聊产品方向：及格 2/2" in text
    assert "总分：1/2 及格" in text


def test_json_report_keeps_every_run_with_its_evidence(tmp_path):
    summaries = report.summarize([result("explain_error", "pass"), result("explain_error", "fail", "没有改动你的项目文件")])
    report.write_json(summaries, tmp_path / "report.json", model_label="m @ host")

    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    [entry] = data["scenarios"]
    assert (data["model"], entry["key"], entry["passed"], entry["scored"]) == ("m @ host", "explain_error", 1, 2)
    assert [r["status"] for r in entry["runs"]] == ["pass", "fail"]
    assert entry["runs"][1]["checks"][0] == {"key": "k0", "label": "没有改动你的项目文件", "ok": False,
                                             "required": True, "evidence": ""}
