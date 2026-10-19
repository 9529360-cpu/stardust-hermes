"""Tool-call validation for the conversation turn loop: unknown tool names (with
auto-repair and the 3-strike partial exit) and malformed JSON arguments (retry, then
recovery tool results).

Role alternation is preserved on every path: an invalid batch is answered with tool-role
error results (never a user message), and the exits close any open tool-result tail
(#48879). Nothing here imports ``agent.conversation_loop`` at module level (cycle).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from agent.message_metadata import append_message
from agent.message_sanitization import close_interrupted_tool_sequence, coalesce_tool_call_id
from agent.turn_failure_copy import site_copy, stamp_failure

logger = logging.getLogger("agent.conversation_loop")


@dataclass
class ToolValidationVerdict:
    """Outcome of ``validate_tool_calls``.

    ``action``: ``"ok"`` (dispatch the calls), ``"continue"`` (re-issue the API call —
    error results / retry state were recorded) or ``"return"`` (terminal partial
    result in ``result``). ``mixed_invalid_batch`` is True when the batch contains BOTH
    valid and unknown tool names: only the invalid calls get error results, the valid
    ones run."""

    action: str
    result: Optional[Dict[str, Any]]
    mixed_invalid_batch: bool



def _preview_name(name: str) -> str:
    return name[:80] + "..." if len(name) > 80 else name


def _append_tool_error_results(messages, tool_calls, content_for) -> None:
    """One tool-role result per call so every tool_call keeps a matching result."""
    for tc in tool_calls:
        append_message(messages, {
            "role": "tool",
            "name": tc.function.name,
            "tool_call_id": coalesce_tool_call_id(tc),
            "content": content_for(tc),
        })


def _partial_exit(agent, messages, conversation_history, api_call_count, final_response: str) -> Dict[str, Any]:
    """Terminal partial result. Prior retries or an earlier tool batch leave a tool-result
    tail; close it as interrupt aborts do so the next turn is not tool→user (#48879).
    This path never reaches finalize_turn, so persist here."""
    close_interrupted_tool_sequence(messages, final_response)
    agent._persist_session(messages, conversation_history)
    return stamp_failure({
        "final_response": final_response,
        "messages": messages,
        "api_calls": api_call_count,
        "completed": False,
        "partial": True,
        "error": final_response,
    }, "truncated", True)


def validate_tool_calls(
    agent: Any, assistant_message: Any, finish_reason: str, *, messages: List[Dict[str, Any]],
    conversation_history: Any, api_call_count: int, effective_task_id: Any,
) -> ToolValidationVerdict:
    """Validate ``assistant_message.tool_calls`` in place (ids uniquified, names
    repaired, dict/empty args normalized to JSON strings). Strikes for invalid names
    advance only when a turn has NO valid call, so a degenerate model still halts at
    3; args cut off mid-stream (routers rewrite ``length`` → ``tool_calls``) are refused
    outright rather than retried."""
    from agent.conversation_loop import _invalid_tool_name_error_content

    tool_calls = assistant_message.tool_calls
    valid_names = agent.valid_tool_names
    agent._json_error_results_this_round = {}
    agent._executable_tool_calls_this_round = None
    agent._invalid_json_halt_after_execution = False

    def _verdict(action: str, result: Optional[Dict[str, Any]] = None, **kwargs) -> ToolValidationVerdict:
        return ToolValidationVerdict(
            action=action, result=result, mixed_invalid_batch=_mixed_invalid_batch, **kwargs
        )

    # Uniquify duplicate tool-call ids BEFORE any downstream consumer: the
    # pre-API sanitizer keeps only the first call/result per id.
    # Assign missing IDs before validation builds per-call error maps. Later
    # transcript-only fallback is too late for multiple malformed anonymous calls.
    for index, tc in enumerate(tool_calls):
        if not coalesce_tool_call_id(tc):
            tc.id = f"call_missing_{index}"
    # Uniquify also resolves collisions with provider-supplied fallback-like IDs.
    agent._uniquify_tool_call_ids(tool_calls)

    # Repair mismatched tool names before validating (model hallucinations).
    for tc in tool_calls:
        if tc.function.name not in valid_names:
            repaired = agent._repair_tool_call(tc.function.name)
            if repaired:
                print(f"{agent.log_prefix}🔧 Auto-repaired tool name: '{tc.function.name}' -> '{repaired}'")
                tc.function.name = repaired
    invalid_tool_calls = [tc.function.name for tc in tool_calls if tc.function.name not in valid_names]
    # Mixed batch: error-result ONLY the invalid calls and run the valid
    # ones; voiding the turn discards real work. Track invalid names
    # independently so useful siblings cannot reset their retry limits.
    _mixed_invalid_batch = bool(invalid_tool_calls) and any(
        tc.function.name in valid_names for tc in tool_calls
    )
    invalid_retry_counts = getattr(agent, "_invalid_tool_retry_counts", None)
    if invalid_retry_counts is None:
        invalid_retry_counts = agent._invalid_tool_retry_counts = {}
    for name in set(invalid_tool_calls):
        invalid_retry_counts[name] = invalid_retry_counts.get(name, 0) + 1
    exhausted_invalid_names = {
        name for name in invalid_tool_calls if invalid_retry_counts[name] >= 3
    }
    agent._invalid_tool_names_exhausted_this_round = exhausted_invalid_names
    if _mixed_invalid_batch:
        _n_valid = sum(1 for tc in tool_calls if tc.function.name in valid_names)
        agent._buffer_vprint(
            f"⚠️  Unknown tool '{_preview_name(invalid_tool_calls[0])}' in batch — erroring that call, "
            f"executing {_n_valid} valid call(s)"
        )
    elif invalid_tool_calls:
        agent._invalid_tool_retries = max(invalid_retry_counts[name] for name in invalid_tool_calls)
        # Return helpful error to model — model can agent-correct next turn
        invalid_preview = _preview_name(invalid_tool_calls[0])
        agent._buffer_vprint(f"⚠️  Unknown tool '{invalid_preview}' — sending error to model for agent-correction ({agent._invalid_tool_retries}/3)")

        if exhausted_invalid_names:
            agent._flush_status_buffer()
            agent._vprint(f"{agent.log_prefix}❌ Max retries (3) for invalid tool calls exceeded. Stopping as partial.", force=True)
            return _verdict("return", _partial_exit(
                agent, messages, conversation_history, api_call_count,
                f"Model generated invalid tool call: {invalid_preview}",
            ))

        append_message(messages, agent._build_assistant_message(assistant_message, finish_reason))
        # See _invalid_tool_name_error_content for the blank-name anti-priming rationale (#47967).
        _append_tool_error_results(
            messages, tool_calls,
            lambda tc: (
                _invalid_tool_name_error_content(tc.function.name, valid_names)
                if tc.function.name not in valid_names and tc.function.name not in exhausted_invalid_names
                else "Skipped: this invalid tool name reached its retry limit; valid sibling calls may still run."
                if tc.function.name in exhausted_invalid_names
                else "Skipped: another tool call in this turn used an invalid name. Please retry this tool call."
            ),
        )
        return _verdict("continue")
    # Successful tool-name validation resets the consecutive homogeneous
    # counter; per-name counts remain available for mixed batches this turn.
    if not invalid_tool_calls:
        agent._invalid_tool_retries = 0

    # Validate tool call arguments are valid JSON; empty strings become empty
    # objects (common model quirk).
    invalid_json_args = []
    for tc in tool_calls:
        args = tc.function.arguments
        if isinstance(args, (dict, list)):
            tc.function.arguments = json.dumps(args)
            continue
        if args is not None and not isinstance(args, str):
            tc.function.arguments = args = str(args)
        if not args or not args.strip():
            tc.function.arguments = "{}"
            continue
        try:
            json.loads(args)
        except json.JSONDecodeError as e:
            # A mixed-batch invalid-name call never executes (error result later);
            # don't let its broken args trigger the whole-turn JSON retry.
            if tc.function.name in valid_names:
                invalid_json_args.append((coalesce_tool_call_id(tc), str(e)))

    if invalid_json_args:
        invalid_call_ids = {call_id for call_id, _ in invalid_json_args}
        # Routers may rewrite finish_reason "length" → "tool_calls", hiding
        # truncation; args not ending in } or ] (stripped) were cut off
        # mid-stream.
        def _looks_truncated(tc) -> bool:
            raw = (tc.function.arguments or "").rstrip()
            try:
                json.loads(raw)
                return False
            except json.JSONDecodeError as exc:
                return exc.msg in {
                    "Expecting value", "Unterminated string starting at", "Expecting ',' delimiter",
                    "Expecting property name enclosed in double quotes",
                } and (
                    exc.pos >= len(raw) or exc.msg == "Unterminated string starting at"
                )

        _truncated = any(
            _looks_truncated(tc) for tc in tool_calls if coalesce_tool_call_id(tc) in invalid_call_ids
        )
        if _truncated:
            agent._vprint(
                f"{agent.log_prefix}⚠️  Truncated tool call arguments detected "
                f"(finish_reason={finish_reason!r}) — refusing to execute.",
                force=True,
            )
            agent._invalid_json_retries = 0
            agent._cleanup_task_resources(effective_task_id)
            return _verdict("return", _partial_exit(
                agent, messages, conversation_history, api_call_count, site_copy("truncated"),
            ))

        agent._invalid_json_retries += 1
        invalid_call_id, error_msg = invalid_json_args[0]
        tool_name = next(tc.function.name for tc in tool_calls if coalesce_tool_call_id(tc) == invalid_call_id)
        agent._buffer_vprint(f"⚠️  Invalid JSON in tool call arguments for '{tool_name}': {error_msg}")

        # Return the validation error immediately so the retry carries new information
        # instead of resending the identical request. The counter still bounds this
        # recovery path to three malformed responses.
        agent._buffer_vprint(f"🔄 Retrying API call ({agent._invalid_json_retries}/3)...")
        agent._buffer_vprint("⚠️  Injecting recovery tool results for invalid JSON...")
        errors_by_id = dict(invalid_json_args)
        valid_calls = [tc for tc in tool_calls if tc.function.name in valid_names and coalesce_tool_call_id(tc) not in invalid_call_ids]
        error_results = {
            coalesce_tool_call_id(tc): (
                f"Error: Invalid JSON arguments. {errors_by_id[coalesce_tool_call_id(tc)]}. "
                "For tools with no required parameters, use an empty object: {}. Please retry with valid JSON."
            )
            for tc in tool_calls if coalesce_tool_call_id(tc) in invalid_call_ids
        }
        if not valid_calls:
            agent._json_error_results_this_round = error_results
            agent._executable_tool_calls_this_round = []
            agent._invalid_json_halt_after_execution = agent._invalid_json_retries >= 3
            return _verdict("continue")
        agent._invalid_json_halt_after_execution = agent._invalid_json_retries >= 3
        agent._json_error_results_this_round = error_results
        agent._executable_tool_calls_this_round = valid_calls
        return _verdict("ok")

    # Reset retry counter on successful JSON validation
    agent._invalid_json_retries = 0
    return _verdict("ok")
