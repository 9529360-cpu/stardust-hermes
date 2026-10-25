"""Ignored messaging nudges annotate both outgoing text and transcript once."""
from types import SimpleNamespace

import pytest

from agent.turn_stop_gates import apply_stop_gates
from agent.verification_stop import _unverified_claim_note, max_iterations_unverified_note

NOTE = _unverified_claim_note(budget_exhausted=False)


def test_ignored_nudge_wording():
    assert NOTE == '⚠️ 注意：本回复中关于任务结果的表述未经本轮工具输出核实，请以实际执行记录为准。'


def test_budget_exhausted_wording_unchanged(monkeypatch):
    monkeypatch.setenv('STARDUST_SESSION_PLATFORM', 'telegram')
    note = max_iterations_unverified_note(
        summary_text='已完成，文件已保存。',
        messages=[{'role': 'user', 'content': '请保存文件'}],
        current_turn_user_idx=0, user_message='请保存文件',
        config={'agent': {'verify_on_stop_messaging': True}},
    )
    assert note == ('⚠️ 注意：本回复是在工具调用预算耗尽后生成的收尾总结，其中关于任务结果的表述'
                    '未经本轮工具输出核实，请以实际执行记录为准。')


@pytest.mark.parametrize('text,kind', [
    ('已完成，文件已保存并验证通过。', 'claim'),
    ('我无法访问本地文件，请你自己打开后把内容贴给我。', 'refusal'),
])
@pytest.mark.parametrize('enabled', [False, True])
def test_ignored_nudge_marks_final_message(monkeypatch, text, kind, enabled):
    monkeypatch.setenv('STARDUST_SESSION_PLATFORM', 'telegram')
    monkeypatch.setattr('agent.verification_stop.verify_on_stop_messaging_enabled', lambda config=None: enabled)
    monkeypatch.setattr('agent.turn_stop_gates._verify_on_stop_nudge', lambda agent: None)
    messages = [{'role': 'user', 'content': '请读取 result.txt'}]
    agent = SimpleNamespace(
        _persist_user_message_idx=0, _verification_stop_nudges=0,
        _turn_file_mutation_paths=set(),
        _flush_messages_to_session_db=lambda *args: True,
        _emit_interim_assistant_message=lambda *args: None,
        _interim_content_was_streamed=lambda *args: False,
    )
    def apply(value):
        row = {'role': 'assistant', 'content': value}
        verdict = apply_stop_gates(agent, row, final_response=value, messages=messages,
            conversation_history=[], pending_verification_response=None,
            pending_verification_response_previewed=False)
        return verdict, row
    if kind == 'claim' and enabled:
        assert apply(text)[0].continue_turn
    else:
        messages.extend([{'role': 'assistant', 'content': text},
                         {'role': 'user', 'content': '[capability note] The read_file tool IS available'}])
        agent._capability_refusal_continuations = 1
    verdict, row = apply(text)
    expected = text + '\n\n' + NOTE if enabled else text
    assert not verdict.continue_turn
    assert verdict.final_response == row['content'] == expected
    verdict, row = apply(expected)
    assert verdict.final_response == row['content'] == expected


@pytest.mark.parametrize('response,evidence', [
    ('尚未完成，未验证。', []),
    ('已读取。', [{'role': 'tool', 'name': 'read_file', 'content': '{"content":"42","status":"success"}'}]),
    ('我无法访问本地文件：文件不存在。', [{'role': 'tool', 'name': 'read_file', 'content': '{"error":"File not found"}'}]),
])
def test_obeyed_nudge_has_no_note(monkeypatch, response, evidence):
    monkeypatch.setenv('STARDUST_SESSION_PLATFORM', 'telegram')
    monkeypatch.setattr('agent.verification_stop.verify_on_stop_messaging_enabled', lambda config=None: True)
    monkeypatch.setattr('agent.turn_stop_gates._verify_on_stop_nudge', lambda agent: None)
    messages = [{'role': 'user', 'content': '请读取 result.txt'},
                {'role': 'assistant', 'content': '已读取'},
                {'role': 'user', 'content': 'verify', '_verification_stop_synthetic': True}, *evidence]
    agent = SimpleNamespace(_persist_user_message_idx=0, _verification_stop_nudges=1,
                            _turn_file_mutation_paths=set())
    row = {'role': 'assistant', 'content': response}
    verdict = apply_stop_gates(agent, row, final_response=response, messages=messages,
        conversation_history=[], pending_verification_response=None,
        pending_verification_response_previewed=False)
    assert verdict.final_response == row['content'] == response
