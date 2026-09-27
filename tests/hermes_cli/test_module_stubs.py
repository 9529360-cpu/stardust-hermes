from __future__ import annotations

import sys
from types import ModuleType

from tests.hermes_cli._module_stubs import patch_modules_only


def test_patch_modules_only_preserves_unrelated_imports_and_restores_owned_entries():
    owned_name = "_stardust_test_owned_module"
    unrelated_name = "_stardust_test_unrelated_module"
    original = ModuleType(owned_name)
    replacement = ModuleType(owned_name)
    unrelated = ModuleType(unrelated_name)
    sys.modules[owned_name] = original
    sys.modules.pop(unrelated_name, None)

    try:
        with patch_modules_only({owned_name: replacement}):
            assert sys.modules[owned_name] is replacement
            sys.modules[unrelated_name] = unrelated

        assert sys.modules[owned_name] is original
        assert sys.modules[unrelated_name] is unrelated
    finally:
        sys.modules.pop(owned_name, None)
        sys.modules.pop(unrelated_name, None)


def test_patch_modules_only_removes_stub_that_did_not_exist_before():
    name = "_stardust_test_new_stub"
    stub = ModuleType(name)
    sys.modules.pop(name, None)

    with patch_modules_only({name: stub}):
        assert sys.modules[name] is stub

    assert name not in sys.modules
