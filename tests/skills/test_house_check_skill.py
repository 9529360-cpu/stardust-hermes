"""Behaviour tests for skills/productivity/house-check/scripts/house_check.py.

The script reports disk space, cache sizes, and the Stardust data home without changing
anything. Every test points the home, cache, and drive lookups at temp directories, so nothing
reads or writes the real home. Symlink and junction cases use real links when the host allows
them; the symlink case is skipped with the repo's require_symlinks marker when it cannot run.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import sys
import time
from collections import namedtuple
from dataclasses import asdict
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "skills" / "productivity" / "house-check"
SCRIPT = SKILL_DIR / "scripts" / "house_check.py"
SKILL_MD = SKILL_DIR / "SKILL.md"
GB = 1024**3
Usage = namedtuple("Usage", "total used free")


def _load_script(name: str = "house_check_under_test"):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up by name while it executes
    spec.loader.exec_module(module)
    return module


house_check = _load_script()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point every home, cache, and drive lookup at temp directories. Returns (home, data_home)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    data_home = tmp_path / "stardust-home"
    monkeypatch.setenv("STARDUST_HOME", str(data_home))
    for spec in house_check.CACHES:
        for variable in spec.env_vars:
            monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(house_check, "list_drive_roots", lambda: [])
    return home, data_home


def _write(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


def _budget(max_entries: int = 10**6, seconds: float = 60.0):
    return house_check.Budget(max_entries=max_entries, deadline=time.monotonic() + seconds)


def _storage_row(*, name="uv", kind="cache", size=0, complete=True, path="/unused", clean_command=None):
    return house_check.StorageRow(
        kind=kind,
        name=name,
        path=path,
        display_path=path,
        path_source="default",
        env_var=None,
        present=True,
        bytes=size,
        entries=1,
        complete=complete,
        stop_reason=None if complete else "file_budget",
        clean_command=clean_command,
    )


def _patch_disk_usage(monkeypatch, outcomes: dict):
    """outcomes maps a drive root to a Usage tuple, or to an exception to raise."""

    def fake_disk_usage(root):
        outcome = outcomes[root]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(house_check.shutil, "disk_usage", fake_disk_usage)


def _snapshot(root: Path) -> dict[str, tuple[int, int, int]]:
    """Every path under root with its size, mtime, and mode. Directory mtimes catch added or removed entries."""
    state = {}
    for current, dirnames, filenames in os.walk(root):
        for name in [*dirnames, *filenames]:
            path = os.path.join(current, name)
            info = os.lstat(path)
            state[path] = (info.st_size, info.st_mtime_ns, info.st_mode)
        info = os.lstat(current)
        state[current] = (info.st_size, info.st_mtime_ns, info.st_mode)
    return state


def _documented_keys() -> dict[str, set[str]]:
    """Read the JSON key table in SKILL.md: object name -> documented keys."""
    keys: dict[str, set[str]] = {}
    in_table = False
    for line in SKILL_MD.read_text(encoding="utf-8").splitlines():
        if line.startswith("| Object |"):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells[0].startswith("---"):
            continue
        obj = cells[0].strip("`").replace("[]", "")
        keys.setdefault(obj, set()).update(re.findall(r"`([a-z_]+)`", cells[1]))
    return keys


# --- walking sizes -----------------------------------------------------------


def test_walk_sums_regular_file_sizes(tmp_path):
    root = tmp_path / "cache"
    _write(root / "a.bin", 100)
    _write(root / "nested" / "b.bin", 250)
    _write(root / "nested" / "deeper" / "c.bin", 1000)

    walk = house_check.walk_size(root, _budget())

    assert walk.bytes == 1350
    assert walk.entries == 5  # a.bin, nested, b.bin, deeper, c.bin
    assert walk.complete is True
    assert walk.stop_reason is None


def test_file_budget_cuts_the_walk_only_past_the_limit(tmp_path):
    root = tmp_path / "cache"
    for index in range(10):
        _write(root / f"f{index}.bin", 100)

    exact = house_check.walk_size(root, _budget(max_entries=10))
    assert exact.complete is True
    assert (exact.bytes, exact.entries) == (1000, 10)

    cut = house_check.walk_size(root, _budget(max_entries=9))
    assert cut.complete is False
    assert cut.stop_reason == "file_budget"
    assert (cut.entries, cut.bytes) == (9, 900)


def test_time_budget_cuts_the_walk_before_it_starts(tmp_path):
    root = tmp_path / "cache"
    _write(root / "a.bin", 100)

    walk = house_check.walk_size(root, house_check.Budget(max_entries=10, deadline=time.monotonic() - 1))

    assert walk.complete is False
    assert walk.stop_reason == "time_budget"
    assert (walk.bytes, walk.entries) == (0, 0)


def test_an_unreadable_folder_is_skipped_and_marks_the_walk_incomplete(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    locked = root / "locked"
    _write(root / "a.bin", 40)
    _write(locked / "hidden.bin", 900)
    real_scandir = os.scandir

    def guarded_scandir(path):
        if os.fspath(path) == str(locked):
            raise PermissionError(13, "access denied")
        return real_scandir(path)

    monkeypatch.setattr(house_check.os, "scandir", guarded_scandir)
    walk = house_check.walk_size(root, _budget())

    assert walk.bytes == 40
    assert walk.complete is False
    assert walk.stop_reason == "unreadable"


def test_the_partial_total_is_reported_as_at_least_in_the_markdown(sandbox):
    _, data_home = sandbox
    for index in range(3):
        _write(data_home / f"state{index}.bin", 10)

    report = house_check.build_report(max_entries=1, max_seconds=60)
    row = report.storage[0]
    markdown = house_check.render_markdown(report)

    assert row.kind == "data" and row.complete is False and row.stop_reason == "file_budget"
    assert "至少" in markdown and "未完成统计：条目数达到上限" in markdown


def test_a_run_out_of_time_marks_its_rows_at_least(sandbox):
    _, data_home = sandbox
    _write(data_home / "state.bin", 4096)

    report = house_check.build_report(max_entries=1000, max_seconds=0)
    markdown = house_check.render_markdown(report)

    assert report.storage[0].complete is False
    assert report.storage[0].stop_reason == "time_budget"
    assert "未完成统计：超过时间上限" in markdown


# --- links are never followed ------------------------------------------------


@pytest.mark.require_symlinks
def test_a_symbolic_link_inside_a_cache_is_not_followed(tmp_path):
    outside = tmp_path / "outside"
    _write(outside / "big.bin", 5000)
    root = tmp_path / "cache"
    _write(root / "small.bin", 10)
    (root / "link").symlink_to(outside, target_is_directory=True)

    walk = house_check.walk_size(root, _budget())

    assert (walk.bytes, walk.complete) == (10, True)


@pytest.mark.windows_only
def test_a_junction_is_not_followed_and_a_junction_root_is_reported(tmp_path):
    import _winapi

    outside = tmp_path / "outside"
    _write(outside / "big.bin", 5000)
    root = tmp_path / "cache"
    _write(root / "small.bin", 10)
    junction = root / "junction"
    _winapi.CreateJunction(str(outside), str(junction))
    try:
        walk = house_check.walk_size(root, _budget())
        assert (walk.bytes, walk.complete) == (10, True)

        present, root_walk = house_check.measure_root(junction, _budget())
        assert present is True
        assert root_walk.complete is False
        assert root_walk.stop_reason == "link_not_followed"
        assert root_walk.bytes == 0
    finally:
        os.rmdir(junction)  # removes the junction only, never the target


# --- cache locations ---------------------------------------------------------


@pytest.mark.parametrize("spec", house_check.CACHES, ids=lambda spec: spec.name)
def test_an_environment_override_wins_over_the_default(sandbox, tmp_path, monkeypatch, spec):
    variable = spec.env_vars[0]
    override = tmp_path / "override" / spec.name
    monkeypatch.setenv(variable, str(override))

    assert house_check.resolve_cache(spec, os.environ) == (override, "env", variable)

    monkeypatch.setenv(variable, "   ")  # blank counts as unset
    assert house_check.resolve_cache(spec, os.environ) == (spec.default_path(), "default", None)


def test_the_report_counts_the_override_and_names_its_variable(sandbox, tmp_path, monkeypatch):
    uv = next(spec for spec in house_check.CACHES if spec.name == "uv")
    _write(uv.default_path() / "stale.bin", 9999)
    override = tmp_path / "custom-uv"
    _write(override / "wheel.bin", 300)
    monkeypatch.setenv("UV_CACHE_DIR", str(override))

    report = house_check.build_report(max_entries=1000, max_seconds=60)
    row = next(row for row in report.storage if row.name == "uv")

    assert (row.bytes, row.path_source, row.env_var) == (300, "env", "UV_CACHE_DIR")
    assert row.path == str(override)


def test_a_missing_cache_is_reported_as_not_found_not_as_an_error(sandbox):
    report = house_check.build_report(max_entries=1000, max_seconds=60)
    missing = [row for row in report.storage if row.name == "Puppeteer"]

    assert missing and missing[0].present is False
    assert missing[0].complete is True and missing[0].bytes == 0


# --- drives ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("free", "total", "status"),
    [
        (5 * GB, 500 * GB, "low"),  # under 10 GB free
        (10 * GB - 1, 50 * GB, "low"),  # just under 10 GB free
        (10 * GB, 50 * GB, "ok"),  # exactly 10 GB free and 20 percent
        (30 * GB, 1000 * GB, "low"),  # 3 percent free
        (100 * GB, 1000 * GB, "ok"),  # exactly 10.0 percent is not below 10 percent
    ],
)
def test_drive_status_follows_the_low_space_rule(monkeypatch, free, total, status):
    _patch_disk_usage(monkeypatch, {"X:\\": Usage(total=total, used=total - free, free=free)})

    assert house_check.probe_drive("X:\\").status == status


def test_a_failing_drive_becomes_an_error_row_and_the_run_continues(sandbox, monkeypatch):
    monkeypatch.setattr(house_check, "list_drive_roots", lambda: ["C:\\", "E:\\"])
    _patch_disk_usage(
        monkeypatch,
        {
            "C:\\": Usage(total=100 * GB, used=50 * GB, free=50 * GB),
            "E:\\": OSError(21, "The device is not ready"),
        },
    )

    report = house_check.build_report(max_entries=1000, max_seconds=60)
    by_drive = {row.drive: row for row in report.drives}

    assert by_drive["C:\\"].status == "ok"
    assert by_drive["E:\\"].status == "error"
    assert by_drive["E:\\"].error == "The device is not ready"
    assert "读取失败：The device is not ready" in house_check.render_markdown(report)


# --- cleanup suggestions -----------------------------------------------------


def test_suggestions_appear_only_at_one_gigabyte(sandbox):
    _, data_home = sandbox
    below = _storage_row(size=GB - 1, clean_command="uv cache clean")
    at = _storage_row(size=GB, clean_command="uv cache clean")

    assert house_check.build_suggestions([below], data_home=data_home) == []
    (suggestion,) = house_check.build_suggestions([at], data_home=data_home)
    assert (suggestion.command, suggestion.command_kind, suggestion.complete) == (
        "uv cache clean",
        "native",
        True,
    )


def test_a_lower_bound_cache_at_the_threshold_is_suggested_and_marked(sandbox):
    _, data_home = sandbox
    row = _storage_row(size=2 * GB, complete=False, clean_command="uv cache clean")

    (suggestion,) = house_check.build_suggestions([row], data_home=data_home)

    assert suggestion.complete is False


def test_the_stardust_data_home_is_never_suggested(sandbox):
    _, data_home = sandbox
    row = _storage_row(kind="data", name="Stardust", size=50 * GB, path=str(data_home))

    assert house_check.build_suggestions([row], data_home=data_home) == []


def test_a_manual_cache_inside_home_gets_a_delete_command(sandbox):
    home, data_home = sandbox
    cache = home / ".some-cache"
    row = _storage_row(name="manual", size=GB, path=str(cache), clean_command=None)

    (suggestion,) = house_check.build_suggestions([row], data_home=data_home)

    assert suggestion.command_kind == "delete_path"
    assert str(cache) in suggestion.command


def test_delete_commands_are_withheld_for_roots_home_and_the_data_home(sandbox, tmp_path):
    home, data_home = sandbox
    unsafe = [
        str(Path(home.anchor)),  # a drive root, or "/"
        str(home),  # the home folder itself
        str(tmp_path),  # an ancestor of both home and the data home
        str(data_home),  # the data home itself
        str(data_home / "cache"),  # inside the data home
    ]
    rows = [_storage_row(name=f"unsafe-{index}", size=GB, path=path) for index, path in enumerate(unsafe)]

    suggestions = house_check.build_suggestions(rows, data_home=data_home)

    assert [item.command_kind for item in suggestions] == ["none"] * len(unsafe)
    assert all(item.command is None for item in suggestions)


# --- output contract ---------------------------------------------------------


def test_default_output_has_the_three_sections_in_order(sandbox, capsys):
    assert house_check.main([]) == 0

    out = capsys.readouterr().out
    positions = [out.index(heading) for heading in ("## 磁盘", "## 缓存与数据", "## 可以考虑清理的")]
    assert positions == sorted(positions)
    assert "没有超过 1 GB 的缓存" in out  # the sandbox holds no cache of 1 GB or more


def test_json_output_has_the_keys_documented_in_skill_md(sandbox, tmp_path, monkeypatch, capsys):
    _, data_home = sandbox
    _write(data_home / "state.bin", 10)
    monkeypatch.setattr(house_check, "list_drive_roots", lambda: [str(tmp_path)])
    documented = _documented_keys()

    assert house_check.main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)

    assert data["schema_version"] == 1
    assert set(data) == documented["top"]
    assert set(data["budgets"]) == {"max_entries_per_row", "max_seconds"}
    assert data["drives"] and set(data["drives"][0]) == documented["drives"]
    assert set(data["storage"][0]) == documented["storage"]
    suggestion = house_check.build_suggestions(
        [_storage_row(size=GB, clean_command="uv cache clean")], data_home=data_home
    )[0]
    assert set(asdict(suggestion)) == documented["suggestions"]


def test_a_run_leaves_the_scanned_tree_unchanged(sandbox, tmp_path, monkeypatch, capsys):
    _, data_home = sandbox
    _write(data_home / "sessions" / "one.json", 120)
    _write(data_home / "logs" / "run.log", 300)
    cache = tmp_path / "custom-npm"
    _write(cache / "pkg" / "index.bin", 800)
    monkeypatch.setenv("npm_config_cache", str(cache))
    monkeypatch.setattr(house_check, "list_drive_roots", lambda: [str(tmp_path)])
    before = _snapshot(tmp_path)

    assert house_check.main(["--json"]) == 0
    capsys.readouterr()

    assert _snapshot(tmp_path) == before


def test_negative_budgets_are_refused(sandbox):
    with pytest.raises(SystemExit) as refused:
        house_check.main(["--max-entries", "-1"])
    assert refused.value.code == 2


# --- data home ---------------------------------------------------------------


def test_the_data_home_is_whatever_get_hermes_home_returns(sandbox, tmp_path, monkeypatch):
    legacy = tmp_path / "legacy-hermes"
    _write(legacy / "state.bin", 500)
    monkeypatch.setenv("HERMES_HOME", str(legacy))  # the script never reads this variable itself
    resolved = tmp_path / "resolved-home"
    _write(resolved / "state.bin", 70)
    monkeypatch.setattr(house_check, "get_hermes_home", lambda: resolved)

    report = house_check.build_report(max_entries=1000, max_seconds=60)
    (data_row,) = [row for row in report.storage if row.kind == "data"]

    assert data_row.path == str(resolved)
    assert (data_row.bytes, data_row.path_source) == (70, "runtime")
    assert report.data_home_line == f"数据目录：{resolved}"


def test_without_the_runtime_resolver_the_data_home_is_unknown(sandbox, monkeypatch):
    _, data_home = sandbox
    _write(data_home / "state.bin", 4096)  # would be measured if the script guessed a location
    monkeypatch.setitem(sys.modules, "hermes_constants", None)  # makes the runtime import fail
    module = _load_script("house_check_without_resolver")

    assert module.get_hermes_home is None
    report = module.build_report(max_entries=1000, max_seconds=60)
    markdown = module.render_markdown(report)

    assert report.data_home_line == "数据目录：无法确定（get_hermes_home 不可用）"
    assert "数据目录：无法确定（get_hermes_home 不可用）" in markdown
    assert all(row.kind == "cache" for row in report.storage)
    assert str(data_home) not in markdown
    manual = _storage_row(name="manual", size=GB, path=str(data_home / "cache"), clean_command=None)
    (suggestion,) = module.build_suggestions([manual], data_home=None)
    assert suggestion.command_kind == "none"


def test_the_data_home_line_shows_the_path_in_display_form(sandbox):
    home, _ = sandbox

    assert house_check.data_home_line(home / ".stardust") == "数据目录：~/.stardust"
    assert house_check.data_home_line(None) == "数据目录：无法确定（get_hermes_home 不可用）"


# --- policy ------------------------------------------------------------------


def test_the_script_imports_only_the_standard_library_and_the_repo_home_helpers():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    assert not imported & {"subprocess", "socket", "urllib", "http", "requests", "ssl"}
    assert all(name in sys.stdlib_module_names or name == "hermes_constants" for name in imported)
