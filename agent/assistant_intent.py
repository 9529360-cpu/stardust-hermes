"""Intent-to-execution policy contract for Stardust's long-lived parent assistant.

This module is deliberately pure: it defines product semantics used by prompt assembly,
without owning execution state, tool routing, delegation state, cron, or kanban.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Literal


Durability = Literal["turn", "process_local", "restart_durable"]


class AssistantExecutionMode(str, Enum):
    RESPOND = "respond"
    EXECUTE_FOREGROUND = "execute_foreground"
    DELEGATE = "delegate"
    DELEGATE_BACKGROUND = "delegate_background"
    SCHEDULE_OR_WATCH = "schedule_or_watch"
    CLARIFY = "clarify"


@dataclass(frozen=True)
class AssistantExecutionContract:
    mode: AssistantExecutionMode
    durability: Durability
    background: bool
    description: str


EXECUTION_CONTRACTS = (
    AssistantExecutionContract(
        AssistantExecutionMode.RESPOND,
        "turn",
        False,
        "Answer, explain, brainstorm, review, or advise in the parent conversation without inventing an action task.",
    ),
    AssistantExecutionContract(
        AssistantExecutionMode.EXECUTE_FOREGROUND,
        "turn",
        False,
        "Use the current authorized tool surface for bounded work that should complete while the user stays in the turn.",
    ),
    AssistantExecutionContract(
        AssistantExecutionMode.DELEGATE,
        "process_local",
        False,
        "Use a scoped child worker for bounded coding, research, or multi-step work while the parent remains the orchestrator.",
    ),
    AssistantExecutionContract(
        AssistantExecutionMode.DELEGATE_BACKGROUND,
        "process_local",
        True,
        "Run bounded work without holding the foreground, but never promise that process-local delegation survives restart.",
    ),
    AssistantExecutionContract(
        AssistantExecutionMode.SCHEDULE_OR_WATCH,
        "restart_durable",
        True,
        "Use cron, kanban, or another durable owner for future, recurring, monitored, or restart-surviving work.",
    ),
    AssistantExecutionContract(
        AssistantExecutionMode.CLARIFY,
        "turn",
        False,
        "Ask only when a material user decision, missing credential, authorization, recipient, or other safety-critical fact blocks progress.",
    ),
)


def contract_for(mode: AssistantExecutionMode) -> AssistantExecutionContract:
    return next(contract for contract in EXECUTION_CONTRACTS if contract.mode is mode)


ASSISTANT_EXECUTION_GUIDANCE = (
    "Choose the smallest execution mode that satisfies the user's intent: `respond` for conversation, "
    "explanation, brainstorming, review, and advice; `execute_foreground` for bounded authorized work on the "
    "current tool surface; `delegate` for scoped coding, research, or multi-step child work while the parent "
    "remains the orchestrator; `delegate_background` for process-local work that can run without holding the "
    "foreground, never as a promise of restart durability; `schedule_or_watch` for future, recurring, monitored, "
    "or restart-surviving work through cron, kanban, or another durable owner; and `clarify` only when a material "
    "user decision, credential, authorization, recipient, or safety-critical fact is genuinely missing. "
    "Background work must remain observable and must not steal focus."
)
