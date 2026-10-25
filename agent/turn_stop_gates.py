"""Text-response stop gates for the conversation turn loop.

When the model stops with a text answer, two gates may instead append the answer as an
interim row plus a synthetic user-role nudge and continue the turn: verify-on-stop (#65919),
the ``pre_verify`` plugin hook after code edits.
Each keeps the candidate answer as a budget-exhaustion fallback
(``pending_verification_response``) and clears ``final_response`` so the finalizer can tell
this gate from error exits (#61631). Nothing here imports ``agent.conversation_loop`` at
module level (cycle).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from agent.message_metadata import append_message

logger = logging.getLogger("agent.conversation_loop")


@dataclass
class StopGateVerdict:
    """``continue_turn`` True → a nudge was appended; re-enter the turn loop with
    ``final_response=None`` and the pending-verification fields updated."""

    continue_turn: bool
    final_response: Any
    pending_verification_response: Any
    pending_verification_response_previewed: Any
    persistence_failed: bool = False


def _verify_on_stop_nudge(agent) -> Optional[str]:
    try:
        from agent.verification_stop import (
            build_verify_on_stop_nudge, verify_on_stop_enabled
        )

        if verify_on_stop_enabled():
            # Per conversation, not per turn: the per-turn counter restarts with every user message and every
            # background-task notice, so it alone cannot stop the same nudge from repeating.
            told = getattr(agent, "_verification_nudged_states", None)
            if not isinstance(told, set):
                told = agent._verification_nudged_states = set()
            return build_verify_on_stop_nudge(
                session_id=getattr(agent, "session_id", None),
                changed_paths=getattr(agent, "_turn_file_mutation_paths", set()),
                attempts=getattr(agent, "_verification_stop_nudges", 0),
                nudged_states=told,
            )
    except Exception:
        logger.debug("verification stop-loop check failed", exc_info=True)
    return None


def _messaging_claim_gate_nudge(agent, final_response, messages) -> Optional[str]:
    """Opt-in evidence gate for messaging completion claims (DL-P2.8e).

    Shares the ``_verification_stop_nudges`` counter with the edit gate, so at
    most one such nudge fires per turn and the pair stays bounded together.
    Reads the turn boundary from ``_persist_user_message_idx`` (reanchored
    after compaction by the turn context) and the raw user text from the live
    user row (memory sidecar bytes live in ``api_content`` only).
    """
    try:
        from agent.verification_stop import messaging_claim_gate_nudge

        return messaging_claim_gate_nudge(
            final_response=final_response,
            messages=messages,
            current_turn_user_idx=getattr(agent, "_persist_user_message_idx", None),
            user_message=messages[getattr(agent, "_persist_user_message_idx", -1)].get("content")
            if isinstance(getattr(agent, "_persist_user_message_idx", None), int)
            and 0 <= getattr(agent, "_persist_user_message_idx", -1) < len(messages)
            else None,
            attempts=getattr(agent, "_verification_stop_nudges", 0),
        )
    except Exception:
        logger.debug("messaging claim gate check failed", exc_info=True)
        return None


def _pre_verify_nudge(agent, final_response, attempt: int) -> Optional[str]:
    """After code edits a registered ``pre_verify`` hook may keep the agent going one
    more turn; no default continuation cost."""
    _edited = sorted(getattr(agent, "_turn_file_mutation_paths", set()) or [])
    try:
        from agent.verify_hooks import max_verify_nudges
        from hermes_cli.lifecycle import has_hook
        from hermes_cli.plugins import get_pre_verify_continue_message

        if _edited and has_hook("pre_verify") and attempt < max_verify_nudges():
            # Posture is fixed for the session — resolve once + cache.
            coding = getattr(agent, "_resolved_is_coding", None)
            if coding is None:
                from agent.coding_context import is_coding_context
                coding = bool(is_coding_context(platform=getattr(agent, "platform", "") or ""))
                agent._resolved_is_coding = coding
            return get_pre_verify_continue_message(
                session_id=getattr(agent, "session_id", None) or "",
                platform=getattr(agent, "platform", "") or "",
                model=getattr(agent, "model", "") or "", coding=coding, attempt=attempt,
                final_response=final_response, changed_paths=_edited,
            )
    except Exception:
        logger.debug("pre_verify hook check failed", exc_info=True)
    return None


def persist_interim_answer(agent, final_msg, messages, conversation_history, flush_fail_msg: str) -> bool:
    """Persist an interim answer before projecting it or continuing execution.

    Keep failed rows in memory for the existing finalizer/recovery path, but do not
    advertise or build another model request from an uncommitted answer. Legacy
    embedders may return None on a successful flush; explicit False means failure.
    """
    append_message(messages, final_msg)
    try:
        persisted = agent._flush_messages_to_session_db(messages, conversation_history)
    except Exception as exc:
        from hermes_state import classify_persistence_error

        if getattr(agent, "_last_persistence_error_cause", None) is None:
            agent._last_persistence_error_cause = classify_persistence_error(exc)
        logger.warning(flush_fail_msg, exc_info=True)
        persisted = False
    if persisted is False:
        agent._incremental_persistence_failed = True
        if getattr(agent, "_last_persistence_error_cause", None) is None:
            agent._last_persistence_error_cause = "unknown"
        return False
    try:
        agent._emit_interim_assistant_message(final_msg)
    except Exception:
        # A presentation failure must not replay an already durable continuation.
        logger.warning("durable interim answer projection failed", exc_info=True)
    return True


def apply_stop_gates(
    agent: Any, final_msg: Dict[str, Any], *, final_response: Any, messages: List[Dict[str, Any]],
    conversation_history: Any, pending_verification_response: Any,
    pending_verification_response_previewed: Any,
) -> StopGateVerdict:
    """Run verify-on-stop → pre_verify hook, in that order. Nudges
    are user-role rows appended only after the assistant answer row, so role alternation
    holds. Hook lookups are imported lazily from their origin modules (tests patch them
    there)."""

    def _continue(nudge: str, flag: str) -> StopGateVerdict:
        """Append the synthetic nudge row and hand the turn back to the loop."""
        append_message(messages, {"role": "user", "content": nudge, flag: True})
        agent._session_messages = messages
        # Keep the answer only as a budget-exhaustion fallback; clear ``final_response`` so
        # the finalizer can tell this gate from error exits. Mark previewed only if the
        # candidate is reused (#61631).
        return StopGateVerdict(
            continue_turn=True, final_response=None,
            pending_verification_response=final_response,
            pending_verification_response_previewed=agent._interim_content_was_streamed(
                final_response or ""
            ),
        )

    from agent.task_acceptance import nudge as acceptance_nudge, refresh_current_goal
    refresh_current_goal(agent, messages)
    from agent.task_acceptance import check_artifact, artifact_notice
    artifact = check_artifact(agent, messages)
    if artifact is not None and not artifact["task_verified"]:
        final_response = artifact_notice(artifact)
        final_msg["content"] = final_response
    _acceptance_nudge = acceptance_nudge(agent)
    if _acceptance_nudge:
        final_msg["finish_reason"] = "acceptance_required"
        if not persist_interim_answer(
            agent, final_msg, messages, conversation_history, "acceptance interim flush failed"
        ):
            return StopGateVerdict(False, "", None, False, persistence_failed=True)
        return _continue(_acceptance_nudge, "_verification_stop_synthetic")

    _verify_nudge = _verify_on_stop_nudge(agent)
    if _verify_nudge:
        final_msg["finish_reason"] = "verification_required"
        if not persist_interim_answer(
            agent, final_msg, messages, conversation_history, "verify-on-stop interim flush failed"
        ):
            return StopGateVerdict(False, "", None, False, persistence_failed=True)
        agent._verification_stop_nudges = getattr(agent, "_verification_stop_nudges", 0) + 1
        verdict = _continue(_verify_nudge, "_verification_stop_synthetic")
        # Internal nudge: stay silent on the terminal, debug-log only.
        logger.debug("verification stop-loop nudge issued (attempt %d)", agent._verification_stop_nudges)
        return verdict

    _claim_nudge = _messaging_claim_gate_nudge(agent, final_response, messages)
    if _claim_nudge:
        final_msg["finish_reason"] = "verification_required"
        if not persist_interim_answer(
            agent, final_msg, messages, conversation_history, "messaging claim gate interim flush failed"
        ):
            return StopGateVerdict(False, "", None, False, persistence_failed=True)
        agent._verification_stop_nudges = getattr(agent, "_verification_stop_nudges", 0) + 1
        verdict = _continue(_claim_nudge, "_verification_stop_synthetic")
        logger.debug("messaging claim-gate nudge issued (attempt %d)", agent._verification_stop_nudges)
        return verdict

    _attempt = getattr(agent, "_pre_verify_nudges", 0)
    _verify_nudge2 = _pre_verify_nudge(agent, final_response, _attempt)
    if _verify_nudge2:
        final_msg["finish_reason"] = "verify_hook_continue"
        if not persist_interim_answer(
            agent, final_msg, messages, conversation_history, "pre_verify interim flush failed"
        ):
            return StopGateVerdict(False, "", None, False, persistence_failed=True)
        agent._pre_verify_nudges = _attempt + 1
        verdict = _continue(_verify_nudge2, "_pre_verify_synthetic")
        logger.debug("pre_verify nudge issued (attempt %d)", agent._pre_verify_nudges)
        return verdict

    from agent.verification_stop import ignored_nudge_unverified_note

    user_idx = getattr(agent, "_persist_user_message_idx", None)
    note = ignored_nudge_unverified_note(
        final_response=final_response, messages=messages, current_turn_user_idx=user_idx,
        user_message=messages[user_idx].get("content")
        if isinstance(user_idx, int) and 0 <= user_idx < len(messages) else None,
        attempts=getattr(agent, "_verification_stop_nudges", 0),
        refusal_attempts=getattr(agent, "_capability_refusal_continuations", 0),
    )
    if note:
        final_response = final_response + "\n\n" + note
        final_msg["content"] = final_response

    return StopGateVerdict(
        continue_turn=False, final_response=final_response,
        pending_verification_response=pending_verification_response,
        pending_verification_response_previewed=pending_verification_response_previewed,
    )
