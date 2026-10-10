import pytest

from tools.browser_use_compat import (
    capability_matrix,
    compatibility_status,
    structured_action_mapping,
)
from tools.browser_use_snapshot import STRUCTURED_SNAPSHOT_CONTRACT


def test_matrix_records_missing_surfaces_without_claiming_full_parity():
    matrix = capability_matrix()
    assert set(matrix) == {
        "status",
        "console_errors",
        "vision_annotation",
        "structured_actions",
        "structured_snapshot",
        "execution_errors",
    }
    assert matrix["status"]["legacy"] == "browser_status"
    assert matrix["console_errors"]["exec"] == "cdp('Runtime.enable')"
    assert matrix["vision_annotation"]["support"] == "partial"
    assert matrix["structured_actions"]["support"] == "mapping"
    assert (
        matrix["structured_actions"]["exec"]
        == "browser_click_ref/browser_fill_ref + js/cdp"
    )
    assert matrix["structured_snapshot"] == {
        "legacy": "browser_snapshot refs",
        "exec": "browser_snapshot_refs + browser_click_ref/browser_fill_ref",
        "support": "native",
    }
    assert matrix["execution_errors"] == {
        "legacy": "browser_* error JSON",
        "exec": "success/error/error_type",
        "support": "native",
    }


def test_status_is_explicitly_non_host_python():
    status = compatibility_status()
    assert status["contract"] == "stardust.browser-exec-compat.v1"
    assert status["host_python"] is False
    assert status["snapshot_contract"] == STRUCTURED_SNAPSHOT_CONTRACT
    assert "screenshot" in status["actions"]


@pytest.mark.parametrize(
    "action,helper",
    [
        ("click", "browser_click_ref"),
        ("type", "browser_fill_ref"),
        ("evaluate", "js"),
    ],
)
def test_structured_action_mapping_preserves_arguments(action, helper):
    args = {"ref": "@e1", "text": "hello"}
    mapped = structured_action_mapping(action, args)
    assert mapped == {"action": action, "exec_helper": helper, "args": args}
    assert args == {"ref": "@e1", "text": "hello"}


def test_unknown_action_is_not_silently_dropped():
    with pytest.raises(ValueError, match="Unsupported browser action"):
        structured_action_mapping("drag")
