"""Narrow sys.modules patching for CLI tests.

unittest.mock.patch.dict(sys.modules, ...) restores the entire mapping on exit,
which can delete modules imported by background threads while a HermesCLI test
is running. Patch only the entries a test owns so unrelated imports survive.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any


_MISSING = object()


@contextmanager
def patch_modules_only(stubs: Mapping[str, Any]) -> Iterator[None]:
    """Temporarily replace only named module entries and restore only those names."""
    previous = {name: sys.modules.get(name, _MISSING) for name in stubs}
    sys.modules.update(stubs)
    try:
        yield
    finally:
        for name, old in previous.items():
            if old is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
