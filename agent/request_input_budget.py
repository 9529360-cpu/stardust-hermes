"""Opt-in request ceiling; local estimates are not provider token accounting."""
from __future__ import annotations

import json
from typing import Any

from agent.model_metadata import estimate_request_tokens_rough, estimate_tokens_rough


class RequestInputBudgetExceeded(ValueError):
    """The final payload cannot be sent within the configured local budget."""


def enforce_request_input_budget(payload: dict[str, Any], budget: int) -> None:
    """Check final input including separate system/instructions and tool schemas.

    Native/provider-specific item formats use serialized local estimates. No history
    is truncated here: normal preflight owns compression; late expansion stops.
    """
    if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
        return
    messages = payload.get("messages")
    if isinstance(messages, list):
        estimated = estimate_request_tokens_rough(messages, tools=payload.get("tools"))
    else:
        estimated = estimate_tokens_rough(json.dumps(payload.get("input", []), ensure_ascii=False))
        estimated += estimate_request_tokens_rough([], tools=payload.get("tools"))
    for key in ("system", "instructions"):
        value = payload.get(key)
        if value:
            estimated += estimate_tokens_rough(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
    if estimated > budget:
        raise RequestInputBudgetExceeded(
            f"Request input budget exceeded: estimated {estimated} tokens > {budget} budget; "
            "no model request was sent. History retained; reduce input/tool footprint or raise "
            "compression.request_input_budget_tokens."
        )
