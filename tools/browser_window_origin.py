"""Origin of a native approval window, decided from OS window ownership. Windows only.

``tools.browser_control_consent`` accepts a native-dialog click only when the caller reports the
window's origin as ``native_ui``. This module decides that origin from what the operating system
says about a window, never from its pixels: a page can paint a fake dialog, but it cannot make the
OS report a window as a visible Chromium top-level window of the pinned browser instance.

A window is ``native_ui`` only when all of these hold:

- it is visible;
- it is a top-level window, or a window owned by a top-level window;
- its process image is the pinned browser executable, and its process belongs to the launched
  browser instance (same executable is not enough: another Chrome on the machine has it too);
- its class is a Chromium top-level class;
- its owner, when it has one, is a Chromium top-level window of that same instance;
- no renderer surface is its ancestor or its descendant. A renderer surface paints page content,
  so this rejects the browser's own frame, page-created popups and any window that hosts a page.

Anything inside or hosting a renderer surface is ``web_page``. Anything else is ``unknown``. The
consent policy denies both.

``classify_window`` is pure and works on snapshots, so tests use fake window records. Just before a
click, ``decide_click`` checks again that the window at the target point is the one that passed the
check: the same hwnd, pid and class, and no renderer surface is it, its ancestor or its descendant.
``decide_click_at_point`` is the thin Windows wrapper around it. Only ``snapshot_windows``,
``window_at_point`` and that wrapper call Win32. Design note:
docs/developer-guide/browser-control-consent.md.
"""

from __future__ import annotations

import ctypes
import functools
import logging
import ntpath
import sys
from collections import defaultdict
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable, Iterator, Mapping

from tools.browser_control_consent import NATIVE_UI_ORIGIN

logger = logging.getLogger(__name__)

WEB_PAGE_ORIGIN = "web_page"
UNKNOWN_ORIGIN = "unknown"
# Classes of the top-level windows that Chromium creates for its browser frame and its bubbles.
CHROMIUM_TOP_LEVEL_CLASSES = frozenset({"Chrome_WidgetWin_1"})
# Classes of the surface that paints page content. Chrome and Edge share this name.
RENDERER_SURFACE_CLASSES = frozenset({"Chrome_RenderWidgetHostHWND"})

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_IMAGE_PATH_CHARS = 32768
_CLASS_NAME_CHARS = 256


@dataclass(frozen=True)
class WindowRecord:
    """One window as the OS reports it.

    ``parent`` is GetParent: the owner of an owned top-level window, the parent of a child window,
    or 0 for an unowned top-level window. ``top_level`` is True when EnumWindows returned the window.
    """

    hwnd: int
    parent: int
    top_level: bool
    visible: bool
    class_name: str
    pid: int
    image_path: str  # "" when the process cannot be opened
    rect: tuple[int, int, int, int]


@dataclass(frozen=True)
class BrowserInstance:
    """The browser a decision is about. The caller supplies both fields from its launch record."""

    pinned_exe: str  # absolute path of the pinned browser executable; never searched for or guessed
    pids: frozenset[int]  # the launched browser process and its descendants


@dataclass(frozen=True)
class WindowVerdict:
    hwnd: int
    origin: str  # NATIVE_UI_ORIGIN, WEB_PAGE_ORIGIN or UNKNOWN_ORIGIN
    reason: str


@dataclass(frozen=True)
class ClickDecision:
    """Whether one click may be sent. ``reason`` is what gets logged when the click is denied."""

    allowed: bool
    reason: str
    approved_hwnd: int
    hit_hwnd: int  # what window_at_point returned at the target point; 0 when there was no window


def classify_window(hwnd: int, windows: Mapping[int, WindowRecord], instance: BrowserInstance) -> WindowVerdict:
    """Decide one window's origin from a snapshot. Pure: no Win32 calls and no I/O."""
    record = windows.get(hwnd)
    if record is None:
        return _verdict(hwnd, UNKNOWN_ORIGIN, "window is not in the snapshot")
    if not _is_absolute(instance.pinned_exe):
        return _verdict(hwnd, UNKNOWN_ORIGIN, "pinned browser path is not absolute; nothing is guessed")
    if any(_is_renderer(node) for node in _self_and_ancestors(record, windows)):
        return _verdict(hwnd, WEB_PAGE_ORIGIN, "is or sits inside a renderer surface: page content")
    if any(_is_renderer(node) for node in _descendants(hwnd, windows)):
        return _verdict(hwnd, WEB_PAGE_ORIGIN, "hosts a renderer surface: a page window, not a dialog")
    if not record.top_level:
        return _verdict(hwnd, UNKNOWN_ORIGIN, "child window, not a top-level or owned window")
    if not record.visible:
        return _verdict(hwnd, UNKNOWN_ORIGIN, "window is not visible")
    if not _is_instance_process(record, instance):
        return _verdict(hwnd, UNKNOWN_ORIGIN, "process is not part of the pinned browser instance")
    if record.class_name not in CHROMIUM_TOP_LEVEL_CLASSES:
        return _verdict(hwnd, UNKNOWN_ORIGIN, "window class is not a Chromium top-level class")
    if record.parent and not _is_browser_owner(windows.get(record.parent), instance):
        return _verdict(hwnd, UNKNOWN_ORIGIN, "owner is not a top-level window of the pinned browser instance")
    return _verdict(hwnd, NATIVE_UI_ORIGIN, "visible Chromium top-level window of the pinned browser; no renderer surface")


def decide_click(verdict: WindowVerdict, approved: WindowRecord, hit_hwnd: int,
                 windows: Mapping[int, WindowRecord]) -> ClickDecision:
    """Decide one click just before it is sent. Pure: no Win32 calls and no I/O.

    ``verdict`` and ``approved`` are the native_ui verdict and the window record captured when the window
    passed the check. ``hit_hwnd`` is what ``window_at_point`` returned at the target point, and ``windows`` is
    a snapshot taken just before the click. The click may proceed only when the window at the point is the
    approved window, with the same hwnd, pid and class, and no renderer surface is that window, one of its
    ancestors or one of its descendants. Anything else is denied, including a window that never passed the
    native_ui check.
    """
    if verdict.origin != NATIVE_UI_ORIGIN or verdict.hwnd != approved.hwnd:
        return _click(False, "the window did not pass the native_ui check", approved, hit_hwnd)
    if not hit_hwnd:
        return _click(False, "no window at the target point", approved, hit_hwnd)
    hit = windows.get(hit_hwnd)
    if hit is None:
        return _click(False, "the window at the target point is not in the snapshot", approved, hit_hwnd)
    if _touches_renderer(hit, windows):
        return _click(False, "the target point lands on a renderer surface or inside one: page content",
                      approved, hit_hwnd)
    if hit_hwnd != approved.hwnd:
        return _click(False, "a different window is at the target point", approved, hit_hwnd)
    if hit.pid != approved.pid:
        return _click(False, "the window at the target point belongs to another process", approved, hit_hwnd)
    if hit.class_name != approved.class_name:
        return _click(False, "the window at the target point has another class", approved, hit_hwnd)
    return _click(True, "the window at the target point is the window that passed the native_ui check",
                  approved, hit_hwnd)


def _verdict(hwnd: int, origin: str, reason: str) -> WindowVerdict:
    return WindowVerdict(hwnd=hwnd, origin=origin, reason=reason)


def _click(allowed: bool, reason: str, approved: WindowRecord, hit_hwnd: int) -> ClickDecision:
    return ClickDecision(allowed=allowed, reason=reason, approved_hwnd=approved.hwnd, hit_hwnd=hit_hwnd)


def _is_renderer(record: WindowRecord) -> bool:
    return record.class_name in RENDERER_SURFACE_CLASSES


def _touches_renderer(record: WindowRecord, windows: Mapping[int, WindowRecord]) -> bool:
    """True when a renderer surface is the window, one of its ancestors or one of its descendants.

    This is the renderer rule ``classify_window`` applies, so the click-time check asks the same question.
    """
    return (any(_is_renderer(node) for node in _self_and_ancestors(record, windows))
            or any(_is_renderer(node) for node in _descendants(record.hwnd, windows)))


def _is_absolute(path: str) -> bool:
    return bool(path) and ntpath.isabs(path)


def _same_path(first: str, second: str) -> bool:
    """Compare two Windows paths the way Windows does: case-insensitive, with separators normalized."""
    return bool(first) and bool(second) and ntpath.normcase(ntpath.normpath(first)) == ntpath.normcase(
        ntpath.normpath(second))


def _is_instance_process(record: WindowRecord, instance: BrowserInstance) -> bool:
    return record.pid in instance.pids and _same_path(record.image_path, instance.pinned_exe)


def _is_browser_owner(owner: WindowRecord | None, instance: BrowserInstance) -> bool:
    return (owner is not None and owner.top_level and owner.class_name in CHROMIUM_TOP_LEVEL_CLASSES
            and _is_instance_process(owner, instance))


def _self_and_ancestors(record: WindowRecord, windows: Mapping[int, WindowRecord]) -> Iterator[WindowRecord]:
    seen: set[int] = set()
    node: WindowRecord | None = record
    while node is not None and node.hwnd not in seen:
        seen.add(node.hwnd)
        yield node
        node = windows.get(node.parent) if node.parent else None


def _descendants(hwnd: int, windows: Mapping[int, WindowRecord]) -> Iterator[WindowRecord]:
    """Child windows and owned windows below ``hwnd``, followed through their parent links."""
    children: dict[int, list[WindowRecord]] = defaultdict(list)
    for node in windows.values():
        if node.parent:
            children[node.parent].append(node)
    seen = {hwnd}
    pending = list(children[hwnd])
    while pending:
        node = pending.pop()
        if node.hwnd in seen:
            continue
        seen.add(node.hwnd)
        yield node
        pending.extend(children[node.hwnd])


# -- Win32 (Windows only) ---------------------------------------------------------------------

def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("browser window verification runs only on native Windows")


@functools.cache
def _api() -> SimpleNamespace:
    """Win32 entry points with explicit prototypes, so 64-bit window and process handles are not truncated."""
    _require_windows()
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _prototype(user32.EnumWindows, [enum_proc, wintypes.LPARAM], wintypes.BOOL)
    _prototype(user32.EnumChildWindows, [wintypes.HWND, enum_proc, wintypes.LPARAM], wintypes.BOOL)
    _prototype(user32.GetParent, [wintypes.HWND], wintypes.HWND)
    _prototype(user32.GetWindowThreadProcessId, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD)
    _prototype(user32.GetClassNameW, [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int], ctypes.c_int)
    _prototype(user32.GetWindowRect, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL)
    _prototype(user32.IsWindowVisible, [wintypes.HWND], wintypes.BOOL)
    _prototype(user32.WindowFromPoint, [wintypes.POINT], wintypes.HWND)
    _prototype(kernel32.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE)
    _prototype(kernel32.QueryFullProcessImageNameW,
               [wintypes.HANDLE, wintypes.DWORD, ctypes.c_wchar_p, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
    _prototype(kernel32.CloseHandle, [wintypes.HANDLE], wintypes.BOOL)
    return SimpleNamespace(user32=user32, kernel32=kernel32, wintypes=wintypes, enum_proc=enum_proc)


def _prototype(function: Callable, argtypes: list, restype: object) -> None:
    function.argtypes = argtypes
    function.restype = restype


def snapshot_windows() -> dict[int, WindowRecord]:
    """Every top-level and owned window on the desktop, and every descendant of each, read from the OS."""
    api = _api()
    windows: dict[int, WindowRecord] = {}
    images: dict[int, str] = {}
    for top in _collect(api, api.user32.EnumWindows):
        _read_window(api, top, top_level=True, images=images, windows=windows)
        for child in _collect(api, api.user32.EnumChildWindows, top):
            _read_window(api, child, top_level=False, images=images, windows=windows)
    return windows


def window_at_point(x: int, y: int) -> int:
    """The window, child windows included, that the OS would deliver a click to at this screen point. Read-only."""
    api = _api()
    return int(api.user32.WindowFromPoint(api.wintypes.POINT(x, y)) or 0)


def decide_click_at_point(verdict: WindowVerdict, approved: WindowRecord, x: int, y: int) -> ClickDecision:
    """Windows only. Run just before a click at screen point (x, y).

    It looks up the window at the point first, then takes a fresh snapshot, decides with ``decide_click``
    and logs the reason when the click is denied. It does not click: the caller clicks only when the
    decision is allowed.
    """
    hit_hwnd = window_at_point(x, y)
    decision = decide_click(verdict, approved, hit_hwnd, snapshot_windows())
    if not decision.allowed:
        logger.warning("browser click at (%d, %d) denied: %s; approved hwnd %#x, window at point %#x",
                       x, y, decision.reason, decision.approved_hwnd, decision.hit_hwnd)
    return decision


def _collect(api: SimpleNamespace, enumerate_fn: Callable, *args: object) -> list[int]:
    found: list[int] = []

    @api.enum_proc
    def visit(hwnd, _lparam):
        found.append(int(hwnd or 0))
        return True

    enumerate_fn(*args, visit, 0)
    return found


def _read_window(api: SimpleNamespace, hwnd: int, *, top_level: bool, images: dict[int, str],
                 windows: dict[int, WindowRecord]) -> None:
    user32, wintypes = api.user32, api.wintypes
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value not in images:
        images[pid.value] = _image_path(api, pid.value)
    class_name = ctypes.create_unicode_buffer(_CLASS_NAME_CHARS)
    user32.GetClassNameW(hwnd, class_name, _CLASS_NAME_CHARS)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    windows[hwnd] = WindowRecord(
        hwnd=hwnd,
        parent=int(user32.GetParent(hwnd) or 0),
        top_level=top_level,
        visible=bool(user32.IsWindowVisible(hwnd)),
        class_name=class_name.value,
        pid=pid.value,
        image_path=images[pid.value],
        rect=(rect.left, rect.top, rect.right, rect.bottom),
    )


def _image_path(api: SimpleNamespace, pid: int) -> str:
    if not pid:
        return ""
    handle = api.kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = api.wintypes.DWORD(_IMAGE_PATH_CHARS)
        path = ctypes.create_unicode_buffer(_IMAGE_PATH_CHARS)
        if not api.kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            return ""
        return path.value
    finally:
        api.kernel32.CloseHandle(handle)
