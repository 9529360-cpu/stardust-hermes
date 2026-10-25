"""Completion claims exclude process narration and clause-local refusals."""
import ast
from pathlib import Path
import re

import pytest

from agent.verification_stop import contains_completion_claim

NON_CLAIMS = [
    "预算耗尽，已执行到第 8 轮。", "已执行到第3步，还需继续。",
    "已经执行到第 2 轮", "已运行到第三步", "已检查到第3个文件",
    "已读取到第3页", "已验证到第2项", "已经检索到第4批",
    "无法确认任务已完成", "不能说已经修复", "无法判断哪些文件已删除",
    "不能确认文件已发送", "无法验证部署已完成", "不能保证已经安装",
    "尚未完成", "没有修复", "我无法访问本地文件，请你自己打开后把内容贴给我。",
    "I cannot confirm it is done", "I can't say it is fixed", "Unable to verify it was completed",
    "I could not confirm the file was deleted", "I am not done yet",
    "The task is not completed", "The fix is not verified", "I cannot say it succeeded",
    "由于工具调用次数已达上限，我无法读取该文件或检查 Git worktree 状态，因此现在不能可靠地判断哪些工作区应删除，也没有删除任何内容。",
]
CLAIMS = [
    "已完成", "搞定了", "已经修复", "已发送", "文件已删除", "已清理干净",
    "已经部署", "已创建", "已安装", "已更新", "已执行命令", "已运行测试",
    "好了", "全部完成", "已完成全部修复并通过验证。", "Done", "fixed", "The task is complete",
    "File sent", "File deleted", "Changes merged", "Tests verified", "Deployment completed",
    "Configured successfully", "Published", "Updated",
]


def eval_regex():
    tree = ast.parse((Path(__file__).parents[2] / "evals/digital_life/understand_user.py").read_text(encoding="utf-8"))
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "COMPLETION_CLAIM_RE" for t in n.targets))
    return eval(compile(ast.Expression(assignment.value), "<eval-regex>", "eval"), {"re": re})


@pytest.mark.parametrize("text", NON_CLAIMS)
def test_narration_and_refusal_are_not_claims(text):
    assert contains_completion_claim(text) is None
    assert eval_regex().search(text) is None


@pytest.mark.parametrize("text", CLAIMS)
def test_real_completion_claims_remain_detected(text):
    assert contains_completion_claim(text) is not None
    # Preserve every positive supported by the original eval vocabulary.
    if text not in ("好了", "fixed", "File sent",
                    "Changes merged", "Configured successfully", "Published",
                    "File deleted", "Updated"):
        assert eval_regex().search(text) is not None


@pytest.mark.parametrize("text", [
    "无法确认A，但B已删除", "已完成。无法确认另一个任务。",
    "I cannot confirm A; B is fixed", "预算耗尽，已执行到第 8 轮，但修复已完成。",
])
def test_negative_clause_does_not_hide_a_separate_claim(text):
    assert contains_completion_claim(text) is not None
    # The eval's pre-existing lexicon omits "fixed" and only scans the last
    # sentence of each line; F9.4 must not introduce new matches there.
    if text not in ("已完成。无法确认另一个任务。", "I cannot confirm A; B is fixed"):
        assert eval_regex().search(text) is not None


@pytest.mark.parametrize("text", NON_CLAIMS)
def test_no_false_gate_or_budget_note(text, monkeypatch):
    from agent.verification_stop import (
        max_iterations_unverified_note, messaging_claim_gate_nudge,
    )

    monkeypatch.setenv("STARDUST_SESSION_PLATFORM", "telegram")
    kwargs = dict(messages=[{"role": "user", "content": "检查文件"}],
                  current_turn_user_idx=0, user_message="检查文件",
                  config={"agent": {"verify_on_stop_messaging": True}})
    assert messaging_claim_gate_nudge(final_response=text, **kwargs) is None
    assert max_iterations_unverified_note(summary_text=text, **kwargs) is None
