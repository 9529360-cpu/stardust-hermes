from tools.background_task_state import project_background_state, state_flags


def test_basic_kernel_phases_project_to_personal_assistant_states():
    cases = {
        "ready": "queued",
        "scheduled": "scheduled",
        "running": "running",
        "review": "waiting_review",
        "done": "completed",
        "archived": "cancelled",
        "triage": "needs_attention",
    }
    for kernel, public in cases.items():
        assert project_background_state(kernel) == public


def test_todo_distinguishes_dependency_wait_from_plain_queue():
    assert project_background_state("todo", dependencies=["parent-1"]) == "waiting_dependency"
    assert project_background_state("todo") == "queued"


def test_blocked_approval_has_priority_over_generic_block_reason():
    assert project_background_state(
        "blocked",
        pending_approvals=[{"approval_id": "apr-1"}],
        events=[{"kind": "blocked", "payload": {"kind": "needs_input", "reason": "need input"}}],
    ) == "waiting_confirmation"


def test_durable_approval_request_and_denial_change_public_wait_state():
    blocked = {
        "kind": "blocked",
        "payload": {
            "kind": "needs_input",
            "reason": "Waiting for user approval apr-1 before send_message.",
        },
    }
    assert project_background_state(
        "blocked",
        events=[blocked, {"kind": "assistant_approval_requested", "payload": {"approval_id": "apr-1"}}],
    ) == "waiting_confirmation"
    assert project_background_state(
        "blocked",
        events=[
            blocked,
            {"kind": "assistant_approval_requested", "payload": {"approval_id": "apr-1"}},
            {"kind": "assistant_approval_denied", "payload": {"approval_id": "apr-1"}},
        ],
    ) == "waiting_input"


def test_block_kinds_project_without_leaking_kernel_terms():
    assert project_background_state(
        "blocked", events=[{"kind": "blocked", "payload": {"kind": "dependency"}}]
    ) == "waiting_dependency"
    assert project_background_state(
        "blocked", events=[{"kind": "blocked", "payload": {"kind": "needs_input"}}]
    ) == "waiting_input"
    assert project_background_state(
        "blocked", events=[{"kind": "blocked", "payload": {"kind": "capability"}}]
    ) == "needs_attention"
    assert project_background_state(
        "blocked", events=[{"kind": "blocked", "payload": {"kind": "transient"}}]
    ) == "needs_attention"


def test_failure_breaker_projects_to_failed():
    assert project_background_state(
        "blocked",
        events=[
            {"kind": "blocked", "payload": {"reason": "worker failed"}},
            {"kind": "gave_up", "payload": {"error": "boom"}},
        ],
        last_failure_error="boom",
    ) == "failed"


def test_approval_reason_fallback_handles_older_event_without_pending_projection():
    assert project_background_state(
        "blocked",
        events=[{
            "kind": "blocked",
            "payload": {"reason": "Waiting for user approval apr-1 before send_message."},
        }],
    ) == "waiting_confirmation"


def test_unknown_kernel_phase_fails_visible_not_terminal():
    assert project_background_state("future_phase") == "needs_attention"
    assert state_flags("needs_attention") == {
        "terminal": False,
        "waiting": True,
        "needs_user": True,
    }


def test_state_flags_derive_completion_and_user_attention():
    assert state_flags("completed") == {"terminal": True, "waiting": False, "needs_user": False}
    assert state_flags("cancelled") == {"terminal": True, "waiting": False, "needs_user": False}
    assert state_flags("waiting_confirmation") == {"terminal": False, "waiting": True, "needs_user": True}
    assert state_flags("waiting_dependency") == {"terminal": False, "waiting": True, "needs_user": False}
