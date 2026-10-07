import pytest

from tools.browser_use_compat import (
    capability_matrix,
    compatibility_status,
    structured_action_mapping,
)


def test_matrix_records_missing_surfaces_without_claiming_full_parity():
    matrix = capability_matrix()
    assert set(matrix) == {"status", "console_errors", "vision_annotation", "structured_actions"}
    assert matrix["status"]["legacy"] == "browser_status"
    assert matrix["console_errors"]["exec"] == "cdp('Runtime.enable')"
    assert matrix["vision_annotation"]["support"] == "partial"
    assert matrix["structured_actions"]["support"] == "mapping"


def test_status_is_explicitly_non_host_python():
    status = compatibility_status()
    assert status["contract"] == "stardust.browser-exec-compat.v1"
    assert status["host_python"] is False
    assert "screenshot" in status["actions"]


@pytest.mark.parametrize("action,helper", [
    ("click", "js/click_at_xy"),
    ("type", "fill_input/js"),
    ("evaluate", "js"),
])
def test_structured_action_mapping_preserves_arguments(action, helper):
    args = {"ref": "@e1", "text": "hello"}
    mapped = structured_action_mapping(action, args)
    assert mapped == {"action": action, "exec_helper": helper, "args": args}
    assert args == {"ref": "@e1", "text": "hello"}


def test_unknown_action_is_not_silently_dropped():
    with pytest.raises(ValueError, match="Unsupported browser action"):
        structured_action_mapping("drag")
