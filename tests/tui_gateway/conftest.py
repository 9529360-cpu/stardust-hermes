"""Import helpers for TUI gateway tests."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from types import ModuleType

import pytest


@pytest.fixture
def tui_gateway_server_loader() -> Callable[[dict[str, object]], ModuleType]:
    """Import the server with temporary stubs without replacing all of sys.modules."""

    def load(stubs: dict[str, object]) -> ModuleType:
        missing = object()
        original = {name: sys.modules.get(name, missing) for name in stubs}
        try:
            sys.modules.update(stubs)
            return importlib.import_module("tui_gateway.server")
        finally:
            for name, module in original.items():
                if module is missing:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

    return load
