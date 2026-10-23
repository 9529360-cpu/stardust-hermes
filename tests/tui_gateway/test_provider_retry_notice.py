from agent.provider_retry_notice import ProviderRetryNotice
from tui_gateway.agent_callbacks import _agent_cbs
from tui_gateway.method_ctx import rebind


def test_retry_metadata_survives_gateway_callback_rebinding():
    events = []
    callbacks = rebind(_agent_cbs, {
        "_emit": lambda *args: events.append(args),
        "_load_interim_assistant_messages": lambda: False,
    })("d7-session")
    notice = ProviderRetryNotice("raw retry", seconds=6, attempt=2, max_attempts=3)
    callbacks["thinking_callback"](notice)
    assert events == [("thinking.delta", "d7-session", {
        "text": "raw retry", "code": "provider_retry",
        "params": {"seconds": 6, "attempt": 2, "max_attempts": 3},
    })]
    callbacks["thinking_callback"]("spinner")
    assert events[-1][2] == {"text": "spinner"}
