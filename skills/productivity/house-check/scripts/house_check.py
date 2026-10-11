#!/usr/bin/env python3
"""House check: a read-only report of this computer's disk space, caches, and Stardust data.

Three sections: 磁盘 (drives), 缓存与数据 (known caches and the Stardust data home), and
可以考虑清理的 (cleanup suggestions for caches of 1 GB or more). Sizes come from stat calls
only. Files are never opened, symlinks and junctions are never followed, and nothing is
created, deleted, moved, or edited. Cleanup commands are printed for the owner to run; this
script never runs them. It makes no network calls and starts no subprocesses.

Usage:
    house_check.py                  Markdown report (Chinese)
    house_check.py --json           the same data as JSON; the keys are listed in SKILL.md
    house_check.py --max-entries N  entry budget per storage row (default 200000)
    house_check.py --max-seconds S  time budget for the whole run (default 8)
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import shlex
import shutil
import stat
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

try:  # inside the repository: the same functions the runtime uses
    from hermes_constants import display_hermes_home, get_hermes_home
except ImportError:  # standalone copy: the same rules with the standard library only

    def get_hermes_home() -> Path:
        """STARDUST_HOME when set, else the platform default home."""
        value = os.environ.get("STARDUST_HOME", "").strip()
        return Path(value) if value else _platform_default_home()

    def display_hermes_home() -> str:
        return _tilde(get_hermes_home())


GB = 1024**3
LOW_FREE_BYTES = 10 * GB
LOW_FREE_PERCENT = 10.0
SUGGEST_BYTES = GB
DEFAULT_MAX_ENTRIES = 200_000
DEFAULT_MAX_SECONDS = 8.0
SCHEMA_VERSION = 1

FILE_ATTRIBUTE_REPARSE_POINT = 0x0400  # symlinks, junctions, and mount points on Windows
SEM_FAILCRITICALERRORS = 0x0001  # no "no disk in drive" dialog while probing a drive

STOP_FILE_BUDGET = "file_budget"
STOP_TIME_BUDGET = "time_budget"
STOP_UNREADABLE = "unreadable"
STOP_LINK = "link_not_followed"
STOP_TEXT = {
    STOP_FILE_BUDGET: "条目数达到上限",
    STOP_TIME_BUDGET: "超过时间上限",
    STOP_UNREADABLE: "部分内容无法读取",
    STOP_LINK: "是符号链接或连接点，未跟随",
}


# --- default locations -------------------------------------------------------


def _local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", "").strip() or Path.home() / "AppData" / "Local")


def _xdg_cache() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", "").strip() or Path.home() / ".cache")


def _library_caches() -> Path:
    return Path.home() / "Library" / "Caches"


def _platform_default_home() -> Path:
    if sys.platform == "win32":
        return _local_appdata() / "stardust"
    return Path.home() / ".stardust"


@dataclass(frozen=True)
class CacheSpec:
    """One known cache: the first non-empty env var wins, else the platform default."""

    name: str
    env_vars: tuple[str, ...]
    # Keyed by sys.platform ("win32", "darwin"); "*" is used for every other platform.
    defaults: Mapping[str, Callable[[], Path]]
    clean_command: str | None  # the tool's own cleanup command, or None (delete the folder)

    def default_path(self) -> Path:
        return self.defaults.get(sys.platform, self.defaults["*"])()


CACHES: tuple[CacheSpec, ...] = (
    CacheSpec(
        "npm",
        ("npm_config_cache",),
        {"win32": lambda: _local_appdata() / "npm-cache", "*": lambda: Path.home() / ".npm"},
        "npm cache clean --force",
    ),
    CacheSpec(
        "uv",
        ("UV_CACHE_DIR",),
        {"win32": lambda: _local_appdata() / "uv" / "cache", "*": lambda: _xdg_cache() / "uv"},
        "uv cache clean",
    ),
    CacheSpec(
        "pip",
        ("PIP_CACHE_DIR",),
        {
            "win32": lambda: _local_appdata() / "pip" / "Cache",
            "darwin": lambda: _library_caches() / "pip",
            "*": lambda: _xdg_cache() / "pip",
        },
        "pip cache purge",
    ),
    CacheSpec(
        "Electron",
        ("ELECTRON_CACHE",),
        {
            "win32": lambda: _local_appdata() / "electron" / "Cache",
            "darwin": lambda: _library_caches() / "electron",
            "*": lambda: _xdg_cache() / "electron",
        },
        None,
    ),
    CacheSpec(
        "electron-builder",
        ("ELECTRON_BUILDER_CACHE",),
        {
            "win32": lambda: _local_appdata() / "electron-builder" / "Cache",
            "darwin": lambda: _library_caches() / "electron-builder",
            "*": lambda: _xdg_cache() / "electron-builder",
        },
        None,
    ),
    CacheSpec(
        "Puppeteer",
        ("PUPPETEER_CACHE_DIR",),
        {"*": lambda: Path.home() / ".cache" / "puppeteer"},
        None,
    ),
    CacheSpec(
        "Playwright",
        ("PLAYWRIGHT_BROWSERS_PATH",),
        {
            "win32": lambda: _local_appdata() / "ms-playwright",
            "darwin": lambda: _library_caches() / "ms-playwright",
            "*": lambda: _xdg_cache() / "ms-playwright",
        },
        None,
    ),
)


def resolve_cache(spec: CacheSpec, environ: Mapping[str, str]) -> tuple[Path, str, str | None]:
    """Return (path, "env" or "default", the env var used or None)."""
    for var in spec.env_vars:
        value = environ.get(var, "").strip()
        if value:
            return Path(os.path.expanduser(os.path.expandvars(value))), "env", var
    return spec.default_path(), "default", None


# --- size walk ---------------------------------------------------------------


@dataclass(frozen=True)
class Budget:
    max_entries: int
    deadline: float  # time.monotonic() value at which the whole run stops walking


@dataclass
class Walk:
    bytes: int = 0
    entries: int = 0
    complete: bool = True
    stop_reason: str | None = None

    def stop(self, reason: str) -> None:
        self.complete = False
        if self.stop_reason is None:
            self.stop_reason = reason


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def _tally(entry: os.DirEntry[str], walk: Walk, pending: list[str]) -> None:
    try:
        info = entry.stat(follow_symlinks=False)
        if entry.is_symlink() or _is_reparse(info):
            return  # never followed, never counted
        if stat.S_ISDIR(info.st_mode):
            pending.append(entry.path)
        elif stat.S_ISREG(info.st_mode):
            walk.bytes += info.st_size
    except OSError:
        walk.stop(STOP_UNREADABLE)


def walk_size(root: Path | str, budget: Budget) -> Walk:
    """Add up regular-file sizes under a real directory root. Files are stat-ed, never opened;
    links found inside the tree are skipped. measure_root() refuses a link at the root itself."""
    walk = Walk()
    pending = [os.fspath(root)]
    while pending:
        try:
            scanner = os.scandir(pending.pop())
        except OSError:
            walk.stop(STOP_UNREADABLE)
            continue
        with scanner:
            for entry in scanner:
                if time.monotonic() >= budget.deadline:
                    walk.stop(STOP_TIME_BUDGET)
                    return walk
                if walk.entries >= budget.max_entries:
                    walk.stop(STOP_FILE_BUDGET)
                    return walk
                walk.entries += 1
                _tally(entry, walk, pending)
    return walk


def measure_root(path: Path, budget: Budget) -> tuple[bool, Walk]:
    """Return (present, walk) for one storage root. A link at the root is reported, not followed."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False, Walk()
    except OSError:
        return True, Walk(complete=False, stop_reason=STOP_UNREADABLE)
    if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
        return True, Walk(complete=False, stop_reason=STOP_LINK)
    return True, walk_size(path, budget)


# --- drives ------------------------------------------------------------------


def list_drive_roots() -> list[str]:
    """Windows: each drive letter that exists (from the logical-drive mask, which touches no drive).
    Elsewhere: the filesystem root."""
    if sys.platform != "win32":
        return ["/"]
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [f"{chr(ord('A') + bit)}:\\" for bit in range(26) if mask & (1 << bit)]


@contextlib.contextmanager
def _no_error_dialogs() -> Iterator[None]:
    """Keep Windows from blocking on a 'no disk in drive' dialog while a drive is probed."""
    if sys.platform != "win32":
        yield
        return
    kernel32 = ctypes.windll.kernel32
    previous = kernel32.SetErrorMode(SEM_FAILCRITICALERRORS)
    try:
        yield
    finally:
        kernel32.SetErrorMode(previous)


def probe_drive(root: str) -> DriveRow:
    try:
        with _no_error_dialogs():
            usage = shutil.disk_usage(root)
    except OSError as exc:
        return DriveRow(root, "error", None, None, None, exc.strerror or str(exc))
    percent = usage.free * 100 / usage.total if usage.total else 0.0
    low = usage.free < LOW_FREE_BYTES or percent < LOW_FREE_PERCENT
    return DriveRow(root, "low" if low else "ok", usage.free, usage.total, round(percent, 1), None)


# --- cleanup suggestions -----------------------------------------------------


def _delete_is_safe(path: Path, data_home: Path) -> bool:
    """A printed delete command must never reach a drive root, the home folder or its ancestors,
    or any part of the Stardust data home."""
    home = Path.home()
    if path.parent == path or path == home or path in home.parents:
        return False
    return not (path == data_home or path in data_home.parents or data_home in path.parents)


def _delete_command(path: str) -> str:
    if sys.platform == "win32":
        escaped = path.replace("'", "''")
        return f"Remove-Item -LiteralPath '{escaped}' -Recurse"
    return f"rm -rf -- {shlex.quote(path)}"


def _cleanup_command(row: StorageRow, data_home: Path) -> tuple[str | None, str]:
    if row.clean_command:
        return row.clean_command, "native"
    if not _delete_is_safe(Path(row.path), data_home):
        return None, "none"
    return _delete_command(row.path), "delete_path"


def build_suggestions(rows: Sequence[StorageRow], *, data_home: Path) -> list[Suggestion]:
    """Suggestions for caches of SUGGEST_BYTES or more. The Stardust data home is never suggested."""
    suggestions = []
    for row in rows:
        if row.kind != "cache" or row.bytes < SUGGEST_BYTES:
            continue
        command, kind = _cleanup_command(row, data_home)
        suggestions.append(
            Suggestion(row.name, row.bytes, row.complete, command, kind)
        )
    return suggestions


# --- report ------------------------------------------------------------------


@dataclass(frozen=True)
class DriveRow:
    drive: str
    status: str  # "ok", "low", or "error"
    free_bytes: int | None
    total_bytes: int | None
    free_percent: float | None
    error: str | None


@dataclass(frozen=True)
class StorageRow:
    kind: str  # "data" for the Stardust home, "cache" for a known cache
    name: str
    path: str
    display_path: str
    path_source: str  # "env" or "default"
    env_var: str | None
    present: bool
    bytes: int
    entries: int
    complete: bool
    stop_reason: str | None
    clean_command: str | None


@dataclass(frozen=True)
class Suggestion:
    name: str
    bytes: int
    complete: bool
    command: str | None
    command_kind: str  # "native", "delete_path", or "none"


@dataclass(frozen=True)
class Report:
    schema_version: int
    generated_at: str
    elapsed_seconds: float
    budgets: dict[str, float | int]
    drives: list[DriveRow]
    storage: list[StorageRow]
    suggestions: list[Suggestion]


def _tilde(path: Path) -> str:
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


def _storage_row(
    kind: str,
    name: str,
    path: Path,
    display: str,
    source: str,
    env_var: str | None,
    clean_command: str | None,
    budget: Budget,
) -> StorageRow:
    present, walk = measure_root(path, budget)
    return StorageRow(
        kind=kind,
        name=name,
        path=str(path),
        display_path=display,
        path_source=source,
        env_var=env_var,
        present=present,
        bytes=walk.bytes,
        entries=walk.entries,
        complete=walk.complete,
        stop_reason=walk.stop_reason,
        clean_command=clean_command,
    )


def build_report(*, max_entries: int = DEFAULT_MAX_ENTRIES, max_seconds: float = DEFAULT_MAX_SECONDS) -> Report:
    started = time.monotonic()
    budget = Budget(max_entries=max_entries, deadline=started + max_seconds)

    home = get_hermes_home()
    home_from_env = bool(os.environ.get("STARDUST_HOME", "").strip())
    storage = [
        _storage_row(
            "data",
            "Stardust 数据目录",
            home,
            display_hermes_home(),
            "env" if home_from_env else "default",
            "STARDUST_HOME" if home_from_env else None,
            None,
            budget,
        )
    ]
    for spec in CACHES:
        path, source, env_var = resolve_cache(spec, os.environ)
        storage.append(
            _storage_row(
                "cache", spec.name, path, _tilde(path), source, env_var, spec.clean_command, budget
            )
        )

    drives = [probe_drive(root) for root in list_drive_roots()]
    return Report(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        elapsed_seconds=round(time.monotonic() - started, 2),
        budgets={"max_entries_per_row": max_entries, "max_seconds": max_seconds},
        drives=drives,
        storage=storage,
        suggestions=build_suggestions(storage, data_home=home),
    )


# --- rendering ---------------------------------------------------------------


def format_bytes(value: int) -> str:
    size, unit = float(value), "B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            break
        size /= 1024
    return f"{size:.0f} B" if unit == "B" else f"{size:.1f} {unit}"


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def _clock(iso_timestamp: str) -> str:
    return datetime.fromisoformat(iso_timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _drive_lines(drives: Sequence[DriveRow]) -> list[str]:
    if not drives:
        return ["未检测到可读取的磁盘。"]
    lines = ["| 盘符 | 可用 | 总量 | 可用比例 | 状态 |", "|---|---|---|---|---|"]
    for drive in drives:
        if drive.status == "error":
            lines.append(f"| `{drive.drive}` | — | — | — | 读取失败：{_cell(drive.error or '')} |")
            continue
        label = "偏低" if drive.status == "low" else "正常"
        lines.append(
            f"| `{drive.drive}` | {format_bytes(drive.free_bytes or 0)} "
            f"| {format_bytes(drive.total_bytes or 0)} | {drive.free_percent:.1f}% | {label} |"
        )
    return lines


def _storage_lines(rows: Sequence[StorageRow]) -> list[str]:
    lines = ["| 项目 | 位置 | 大小 | 条目 | 状况 |", "|---|---|---|---|---|"]
    for row in rows:
        if not row.present:
            size, count, state = "—", "—", "未找到"
        else:
            size = format_bytes(row.bytes) if row.complete else "至少 " + format_bytes(row.bytes)
            count = str(row.entries)
            state = "已统计" if row.complete else f"未完成统计：{STOP_TEXT.get(row.stop_reason or '', '')}"
        lines.append(f"| {row.name} | `{row.display_path}` | {size} | {count} | {state} |")
    return lines


def _suggestion_lines(suggestions: Sequence[Suggestion]) -> list[str]:
    if not suggestions:
        return ["没有超过 1 GB 的缓存，暂时没有清理建议。"]
    lines = []
    for item in suggestions:
        size = f"约 {format_bytes(item.bytes)}" if item.complete else f"至少 {format_bytes(item.bytes)}"
        if item.command_kind == "native":
            advice = f"自行运行：`{item.command}`"
        elif item.command_kind == "delete_path":
            advice = f"先关闭相关程序，再自行运行：`{item.command}`"
        else:
            advice = "路径不适合自动给出删除命令，请自行核对后处理。"
        lines.append(f"- **{item.name}**：{size}。{advice}")
    lines += ["", "以上只是建议，本脚本不会运行任何命令。缓存可以重新下载。"]
    return lines


def render_markdown(report: Report) -> str:
    lines = [
        "# 电脑状态",
        "",
        f"检查时间：{_clock(report.generated_at)}，用时 {report.elapsed_seconds:.1f} 秒。只读检查，没有改动任何文件。",
        "",
        "## 磁盘",
        "",
        *_drive_lines(report.drives),
        "",
        "偏低：可用空间少于 10 GB，或可用比例低于 10%。",
        "",
        "## 缓存与数据",
        "",
        *_storage_lines(report.storage),
        "",
        f"单位按 1024 进制计算（1 GB = 1024 MB）。每个位置最多统计 {report.budgets['max_entries_per_row']} 个条目，"
        f"整次检查最多 {report.budgets['max_seconds']:g} 秒；超出时显示“至少”。",
        "",
        "## 可以考虑清理的",
        "",
        *_suggestion_lines(report.suggestions),
    ]
    return "\n".join(lines) + "\n"


# --- entry point -------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only report of disk space, known caches, and the Stardust data home."
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument(
        "--max-entries",
        type=int,
        default=DEFAULT_MAX_ENTRIES,
        help="entry budget per storage row (default %(default)s)",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=DEFAULT_MAX_SECONDS,
        help="time budget for the whole run (default %(default)s)",
    )
    args = parser.parse_args(argv)
    if args.max_entries < 0 or args.max_seconds < 0:
        parser.error("--max-entries and --max-seconds must not be negative")

    report = build_report(max_entries=args.max_entries, max_seconds=args.max_seconds)
    if args.json:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report), end="")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
