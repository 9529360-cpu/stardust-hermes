"""Element and double-click addressing against cua-driver 0.31's real input schemas.

Field case 2026-10-01 (Windows 11, cua-driver 0.31.0): every element click came back
``click: unknown argument element_index`` and every double-click ``unknown argument button``, so the
agent could only click by pixel coordinates. 0.31's schemas are ``additionalProperties: false``,
address elements by ``element_token`` only, double-click through ``click``'s ``count`` and have no
element-addressed drag. The fixture holds those schemas (names and types only).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

FIXTURE = Path(__file__).parents[1] / "fixtures" / "cua_driver_0_31_input_schemas.json"


class _StrictDriverSession:
    """Answers like cua-driver 0.31: an argument outside a tool's schema is an error."""

    def __init__(self) -> None:
        from tools.computer_use.cua_backend_session import _CuaDriverSession, _AsyncBridge

        self._real = _CuaDriverSession(_AsyncBridge())
        self._real._tool_schemas = json.loads(FIXTURE.read_text(encoding="utf-8"))["tools"]
        self._real._capabilities = {name: set() for name in self._real._tool_schemas}
        self.calls: list[tuple[str, Dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:  # schema/capability queries go to the real session
        return getattr(self._real, name)

    def call_tool(self, name: str, args: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        unknown = sorted(set(args) - set(self._real._tool_schemas[name]["properties"]))
        if unknown:
            return {"isError": True, "data": f"{name}: unknown argument {unknown[0]}",
                    "structuredContent": None, "images": [], "image_mime_types": []}
        self.calls.append((name, dict(args)))
        return {"isError": False, "data": "ok", "structuredContent": None, "images": [], "image_mime_types": []}


@pytest.fixture
def driver():
    from tools.computer_use.cua_backend import CuaDriverBackend

    session = _StrictDriverSession()
    backend = CuaDriverBackend()
    backend._session = session
    backend._active_pid = 42
    backend._active_window_id = 7
    backend._snapshot_tokens = {5: "s00000003:5"}
    return backend, session


@pytest.mark.parametrize("act, tool", [
    (lambda b: b.click(element=5), "click"),
    (lambda b: b.click(element=5, button="right"), "click"),
    (lambda b: b.scroll(direction="down", element=5), "scroll"),
    (lambda b: b.set_value("abc", element=5), "set_value"),
])
def test_element_actions_address_the_element_by_token(driver, act, tool):
    backend, session = driver
    result = act(backend)

    assert result.ok, result.message
    name, args = session.calls[-1]
    assert name == tool
    assert args["element_token"] == "s00000003:5"
    assert "element_index" not in args


@pytest.mark.parametrize("target", [{"element": 5}, {"x": 10, "y": 20}])
def test_double_click_reaches_the_driver(driver, target):
    backend, session = driver
    result = backend.click(click_count=2, **target)

    assert result.ok, result.message
    name, args = session.calls[-1]
    assert name == "click" and args["count"] == 2


def test_element_missing_from_the_latest_capture_is_refused_before_the_driver(driver):
    backend, session = driver
    result = backend.click(element=9)

    assert not result.ok
    assert "capture" in result.message
    assert session.calls == []


def test_element_drag_is_refused_with_a_coordinate_hint(driver):
    backend, session = driver
    result = backend.drag(from_element=5, to_element=5)

    assert not result.ok
    assert "coordinate" in result.message
    assert session.calls == []


def test_coordinate_scroll_is_sent_when_the_schema_takes_coordinates(driver):
    backend, session = driver
    result = backend.scroll(direction="down", x=10, y=20)

    assert result.ok, result.message
    _name, args = session.calls[-1]
    assert (args["x"], args["y"]) == (10, 20)
