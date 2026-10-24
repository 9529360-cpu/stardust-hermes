"""File acceptance sanitizes the stop candidate without changing turn completion."""
from types import SimpleNamespace

from agent import task_acceptance
from agent.turn_stop_gates import apply_stop_gates


def test_unverified_stop_replaces_claim_before_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr('agent.turn_stop_gates._verify_on_stop_nudge', lambda agent: None)
    monkeypatch.setattr('agent.turn_stop_gates._pre_verify_nudge', lambda *args: None)
    agent = SimpleNamespace(_artifact_contract=task_acceptance.artifact_contract(
        'Create result.txt containing 42 and read it back', tmp_path))
    candidate = {'role': 'assistant', 'content': 'Saved and verified.'}
    verdict = apply_stop_gates(agent, candidate, final_response=candidate['content'],
                              messages=[], conversation_history=[],
                              pending_verification_response=None,
                              pending_verification_response_previewed=False)
    assert verdict.continue_turn is False
    assert 'Task not verified' in verdict.final_response
    assert '未验收' in candidate['content']
    assert 'Saved and verified' not in candidate['content']


def test_no_contract_leaves_stop_candidate_byte_identical(monkeypatch):
    monkeypatch.setattr('agent.turn_stop_gates._verify_on_stop_nudge', lambda agent: None)
    monkeypatch.setattr('agent.turn_stop_gates._pre_verify_nudge', lambda *args: None)
    candidate = {'role': 'assistant', 'content': 'Saved and verified.\n42'}
    before = dict(candidate)
    verdict = apply_stop_gates(SimpleNamespace(), candidate, final_response=candidate['content'],
                              messages=[], conversation_history=[],
                              pending_verification_response=None,
                              pending_verification_response_previewed=False)
    assert candidate == before
    assert verdict.final_response.encode() == before['content'].encode()
    assert verdict.continue_turn is False
