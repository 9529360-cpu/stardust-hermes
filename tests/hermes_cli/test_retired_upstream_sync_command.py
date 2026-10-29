"""Retired Nous skill sync: no client ships, and the old command only explains."""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RETIRED_MODULES = ("skills_sync_client", "skills_sync_client_org", "skills_sync_client_wire")


@pytest.mark.parametrize("module_name", RETIRED_MODULES)
def test_upstream_sync_modules_are_not_shipped(module_name):
    # Checked on disk: an editable install of another checkout can still resolve a deleted module by name.
    assert not (REPO_ROOT / "tools" / f"{module_name}.py").exists()


def test_sync_command_is_retired_without_loading_an_upstream_client(capsys):
    from hermes_cli.main_platform_setup import cmd_sync

    assert cmd_sync(SimpleNamespace(legacy_args=["push"])) == 1
    assert "retired" in capsys.readouterr().err.lower()
    assert not [name for name in sys.modules if name.startswith("tools.skills_sync_client")]


@pytest.mark.parametrize("argv", [
    ["sync"],
    ["sync", "now"],
    ["sync", "propose", "my-skill", "-m", "please review"],
    ["sync", "device", "laptop", "--name", "Laptop"],
])
def test_old_sync_arguments_reach_the_retirement_notice(argv):
    from hermes_cli.main import _build_cli_parser
    from hermes_cli.main_platform_setup import cmd_sync

    parser, _subparsers = _build_cli_parser()
    assert parser.parse_args(argv).func is cmd_sync


def test_old_sync_command_exits_nonzero_with_the_notice(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "sync", "now"],
        env={"PATH": "", "STARDUST_HOME": str(tmp_path), "HOME": str(tmp_path),
             "USERPROFILE": str(tmp_path), "SYSTEMROOT": "C:\\Windows", "PYTHONIOENCODING": "utf-8"},
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=50, check=False)
    assert result.returncode == 1
    assert "retired" in result.stderr.lower()
    assert not list(tmp_path.rglob(".sync_*"))
