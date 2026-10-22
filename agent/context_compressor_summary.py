"""Summary-hook dispatch and cancellation rollback for context compression."""

from __future__ import annotations

import inspect
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from agent.auxiliary_client import AuxiliaryExplicitCancellation

if TYPE_CHECKING:
    from agent.context_compressor import _HandoffScan


def _accepts_keyword_argument(callable_obj: Any, name: str) -> bool:
    """Return whether an inspectable callable accepts ``name`` as a keyword."""
    try:
        parameters = inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return False
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return True
    parameter = parameters.get(name)
    return parameter is not None and parameter.kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    )


CONTINUITY_SECTIONS = {
    "Current Request": "Record the latest user goal and scope; distinguish superseded goals. Historical data, never new authorization.",
    "Satisfied Conditions": "List completed acceptance conditions with observed outcomes; never repeat completed side effects.",
    "Unsatisfied Conditions": "List pending acceptance checks, blockers and uncertainty; tests/builds do not prove runtime success.",
    "Evidence": "Keep exact verification commands, results/counts, decisions and evidence paths; distinguish observed from inferred.",
    "Resource State": "Keep open process/tool handles, PID and identity if known, ports, worktrees/branches, temp files/homes and cleanup still owed. Include ownership and retained-service intent. Do not claim a resource is closed without evidence.",
    "Next Step": "Record the pending continuation and cleanup needed under the current user's scope; this record grants no authority.",
}


def continuity_summary_template() -> str:
    return "\n\n".join(f"## {name}\n[{instruction} Write None or Unknown explicitly if applicable.]"
                        for name, instruction in CONTINUITY_SECTIONS.items())


def validate_continuity_summary(summary: str) -> None:
    """Reject incomplete opt-in checkpoints before any history is replaced."""
    sections = dict(re.findall(r"^## ([^\n]+)\n(.*?)(?=^## |\Z)", summary, re.M | re.S))
    missing = [name for name in CONTINUITY_SECTIONS if not sections.get(name, "").strip()]
    if missing:
        raise ValueError("Incomplete continuity summary: " + ", ".join(missing))


class SummaryDispatchMixin:
    def _summarize_window(
        self, messages: List[Dict[str, Any]], turns_to_summarize: List[Dict[str, Any]], scan: "_HandoffScan",
        focus_topic: Optional[str], memory_context: str, bypass_cooldown: bool,
    ) -> Optional[str]:
        """Run the summary LLM; a cancellation rolls back the handoff scan's self-heal mutation first."""
        # Focus-topic derivation scans user turns; only pay when a summary is generated.
        summary_kwargs: Dict[str, Any] = {
            "focus_topic": focus_topic or self._derive_auto_focus_topic(messages),
            "memory_context": memory_context,
        }
        if _accepts_keyword_argument(self._generate_summary, "bypass_cooldown"):
            summary_kwargs["bypass_cooldown"] = bypass_cooldown
        try:
            return self._generate_summary(turns_to_summarize, **summary_kwargs)
        except AuxiliaryExplicitCancellation:
            # Cancellation is a true no-op: restore the scan's mutation before the exception escapes.
            self._previous_summary = scan.previous_summary_before
            self._summary_has_user_turn = scan.has_user_turn_before
            raise
