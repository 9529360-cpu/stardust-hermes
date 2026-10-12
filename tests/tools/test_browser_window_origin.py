"""Native-window origin verifier: fake window records for every branch, then real windows.

Most cases use fake ``WindowRecord`` snapshots, so they run on any host. The ``windows_only`` cases
create real windows in this process with the Chromium class names and read them back through the
OS calls. Nothing here starts a browser, clicks, or reads a profile.
"""

from __future__ import annotations

import ctypes
import logging
import os
from types import SimpleNamespace

import pytest

from tools.browser_control_consent import (
    LAUNCH_RECORD_ATTRIBUTION,
    NATIVE_UI_ORIGIN,
    ApprovalAction,
    ConnectionIdentity,
    ConsentContext,
    NativeDialogClick,
    OWNER_APPROVAL_SOURCE,
    OwnerApproval,
    evaluate,
)
from tools.browser_window_origin import (
    CHROMIUM_TOP_LEVEL_CLASSES,
    RENDERER_SURFACE_CLASSES,
    UNKNOWN_ORIGIN,
    WEB_PAGE_ORIGIN,
    BrowserInstance,
    WindowRecord,
    WindowVerdict,
    classify_window,
    decide_click,
    decide_click_at_point,
    snapshot_windows,
)

CHROMIUM = "Chrome_WidgetWin_1"
RENDERER_CLASS = "Chrome_RenderWidgetHostHWND"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
LOOKALIKE = r"C:\Users\reviewer\Downloads\chrome.exe"
INSTANCE = BrowserInstance(pinned_exe=CHROME, pids=frozenset({4100, 4101}))
MAIN, BUBBLE, POPUP, RENDERER, FAKE, TOOLBAR, HOST, FOREIGN = 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80
OTHER_DIALOG = 0x90
FOREIGN_CLASS = "Notepad"
OTHER_INSTANCE_PID = 5555
FOREIGN_PID = 9999
NOW = 1_000.0
IDENTITY = ConnectionIdentity(
    principal_id="principal:desktop:0f1e2d3c", profile_id="default", session_id="session-a",
    controller_id="controller-a", browser_profile_id="stardust-isolated-1", transport_family="local-api",
)


def top(hwnd, *, owner=0, cls=CHROMIUM, visible=True, pid=4100, image=CHROME):
    """A top-level window, or one owned by ``owner``."""
    return WindowRecord(hwnd=hwnd, parent=owner, top_level=True, visible=visible, class_name=cls,
                        pid=pid, image_path=image, rect=(0, 0, 640, 480))


def child(hwnd, parent, *, cls=CHROMIUM, visible=True, pid=4100, image=CHROME):
    """A child window of ``parent``. Enumerated through EnumChildWindows, so top_level is False."""
    return WindowRecord(hwnd=hwnd, parent=parent, top_level=False, visible=visible, class_name=cls,
                        pid=pid, image_path=image, rect=(0, 0, 320, 240))


def snapshot(*records):
    return {record.hwnd: record for record in records}


def origin_of(target, records, instance=INSTANCE):
    return classify_window(target, records, instance).origin


# -- Native dialogs: what the OS reports as a browser dialog ----------------------------------

def test_dialog_owned_by_the_pinned_frame_is_native_ui():
    assert origin_of(BUBBLE, snapshot(top(MAIN), top(BUBBLE, owner=MAIN))) == NATIVE_UI_ORIGIN


def test_unowned_chromium_dialog_without_a_renderer_is_native_ui():
    assert origin_of(BUBBLE, snapshot(top(BUBBLE))) == NATIVE_UI_ORIGIN


def test_dialog_is_native_even_when_its_owner_hosts_the_page():
    """The owner hosts the renderer. The dialog itself hosts nothing, so it is native."""
    records = snapshot(top(MAIN), child(RENDERER, MAIN, cls=RENDERER_CLASS), top(BUBBLE, owner=MAIN))
    assert origin_of(BUBBLE, records) == NATIVE_UI_ORIGIN


# -- Page content: a renderer surface is, or is an ancestor of, the window --------------------

def test_browser_frame_that_hosts_the_page_is_page_content():
    """The frame is a visible Chromium top-level window of the pinned process. Only the renderer rule rejects it."""
    result = classify_window(MAIN, snapshot(top(MAIN), child(RENDERER, MAIN, cls=RENDERER_CLASS)), INSTANCE)
    assert result.origin == WEB_PAGE_ORIGIN
    assert "hosts" in result.reason


def test_page_created_popup_is_page_content_even_though_it_is_top_level():
    assert origin_of(POPUP, snapshot(top(POPUP), child(RENDERER, POPUP, cls=RENDERER_CLASS))) == WEB_PAGE_ORIGIN


def test_dialog_that_hosts_a_renderer_surface_is_page_content():
    records = snapshot(top(MAIN), top(BUBBLE, owner=MAIN), child(RENDERER, BUBBLE, cls=RENDERER_CLASS))
    assert origin_of(BUBBLE, records) == WEB_PAGE_ORIGIN


def test_page_painted_dialog_under_a_renderer_surface_is_page_content():
    """A page paints a fake dialog inside its renderer. Even a window with the Chromium class and the
    pinned process is page content when a renderer surface is above it."""
    records = snapshot(top(MAIN), child(RENDERER, MAIN, cls=RENDERER_CLASS), child(FAKE, RENDERER, cls=CHROMIUM))
    assert origin_of(FAKE, records) == WEB_PAGE_ORIGIN
    assert origin_of(RENDERER, records) == WEB_PAGE_ORIGIN


def test_renderer_surface_is_page_content_even_when_top_level():
    assert origin_of(RENDERER, snapshot(top(RENDERER, cls=RENDERER_CLASS))) == WEB_PAGE_ORIGIN


def test_renderer_classes_and_top_level_classes_do_not_overlap():
    assert not (RENDERER_SURFACE_CLASSES & CHROMIUM_TOP_LEVEL_CLASSES)


# -- Unknown: anything else is denied ---------------------------------------------------------

def test_child_window_of_the_frame_is_unknown_not_a_dialog():
    assert origin_of(TOOLBAR, snapshot(top(MAIN), child(TOOLBAR, MAIN))) == UNKNOWN_ORIGIN


def test_look_alike_dialog_from_another_process_is_unknown():
    records = snapshot(top(MAIN), top(BUBBLE, owner=MAIN, pid=FOREIGN_PID, image=LOOKALIKE))
    assert origin_of(BUBBLE, records) == UNKNOWN_ORIGIN


def test_dialog_with_the_pinned_image_but_outside_this_instance_is_unknown():
    """Another Chrome on the same machine has the same executable. Only the launched instance counts."""
    records = snapshot(top(MAIN), top(BUBBLE, owner=MAIN, pid=OTHER_INSTANCE_PID))
    result = classify_window(BUBBLE, records, INSTANCE)
    assert result.origin == UNKNOWN_ORIGIN
    assert "instance" in result.reason


def test_dialog_owned_by_a_window_of_another_process_is_unknown():
    records = snapshot(top(FOREIGN, pid=FOREIGN_PID, image=LOOKALIKE), top(BUBBLE, owner=FOREIGN))
    assert origin_of(BUBBLE, records) == UNKNOWN_ORIGIN


def test_owner_with_a_non_chromium_class_is_unknown():
    assert origin_of(BUBBLE, snapshot(top(MAIN, cls="Notepad"), top(BUBBLE, owner=MAIN))) == UNKNOWN_ORIGIN


def test_owner_that_is_a_child_window_is_unknown():
    records = snapshot(top(HOST), child(MAIN, HOST), top(BUBBLE, owner=MAIN))
    assert origin_of(BUBBLE, records) == UNKNOWN_ORIGIN


def test_owner_missing_from_the_snapshot_is_unknown():
    assert origin_of(BUBBLE, snapshot(top(BUBBLE, owner=0x999))) == UNKNOWN_ORIGIN


def test_os_common_dialog_class_is_unknown():
    assert origin_of(BUBBLE, snapshot(top(BUBBLE, cls="#32770"))) == UNKNOWN_ORIGIN


def test_invisible_dialog_is_unknown():
    assert origin_of(BUBBLE, snapshot(top(MAIN), top(BUBBLE, owner=MAIN, visible=False))) == UNKNOWN_ORIGIN


def test_window_that_is_not_in_the_snapshot_is_unknown():
    assert origin_of(0xDEAD, snapshot(top(MAIN))) == UNKNOWN_ORIGIN


@pytest.mark.parametrize("pinned", [
    pytest.param("chrome.exe", id="bare-name"),
    pytest.param("", id="empty"),
    pytest.param(r"Program Files\Google\Chrome\Application\chrome.exe", id="rooted-without-drive"),
])
def test_relative_or_missing_pinned_path_is_unknown_and_nothing_is_guessed(pinned):
    records = snapshot(top(MAIN), top(BUBBLE, owner=MAIN))
    assert origin_of(BUBBLE, records, BrowserInstance(pinned_exe=pinned, pids=frozenset({4100}))) == UNKNOWN_ORIGIN


# -- Path and browser identity ----------------------------------------------------------------

def test_pinned_path_is_compared_the_way_windows_compares_paths():
    records = snapshot(top(MAIN), top(BUBBLE, owner=MAIN, image="c:/program files/google/chrome/application/CHROME.EXE"))
    assert origin_of(BUBBLE, records) == NATIVE_UI_ORIGIN


def test_another_install_of_the_same_browser_is_unknown():
    other_install = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    records = snapshot(top(MAIN, image=other_install), top(BUBBLE, owner=MAIN, image=other_install))
    assert origin_of(BUBBLE, records) == UNKNOWN_ORIGIN


def test_an_edge_instance_accepts_only_edge_processes():
    edge_instance = BrowserInstance(pinned_exe=EDGE, pids=frozenset({4100}))
    assert origin_of(BUBBLE, snapshot(top(MAIN, image=EDGE), top(BUBBLE, owner=MAIN, image=EDGE)),
                     edge_instance) == NATIVE_UI_ORIGIN
    assert origin_of(BUBBLE, snapshot(top(MAIN), top(BUBBLE, owner=MAIN)), edge_instance) == UNKNOWN_ORIGIN


# -- The verdict drives the consent policy ----------------------------------------------------

def _policy_allows(origin: str) -> bool:
    request = NativeDialogClick(identity=IDENTITY, permission="geolocation", origin=origin,
                                attribution=LAUNCH_RECORD_ATTRIBUTION)
    grant = OwnerApproval(
        approval_id="ap-1", source=OWNER_APPROVAL_SOURCE, identity=IDENTITY,
        actions=frozenset({ApprovalAction.CLICK_NATIVE}), native_permissions=frozenset({"geolocation"}),
    )
    return evaluate(request, [grant], context=ConsentContext(now=NOW, isolated_profile_root="")).allowed


@pytest.mark.parametrize(("records", "target", "allowed"), [
    pytest.param(snapshot(top(MAIN), top(BUBBLE, owner=MAIN)), BUBBLE, True, id="native-dialog"),
    pytest.param(snapshot(top(MAIN), child(RENDERER, MAIN, cls=RENDERER_CLASS)), MAIN, False,
                 id="frame-hosting-the-page"),
    pytest.param(snapshot(top(MAIN), child(RENDERER, MAIN, cls=RENDERER_CLASS), child(FAKE, RENDERER)), FAKE,
                 False, id="page-painted-dialog"),
    pytest.param(snapshot(top(MAIN), top(BUBBLE, owner=MAIN, pid=OTHER_INSTANCE_PID)), BUBBLE, False,
                 id="same-exe-other-instance"),
])
def test_only_a_native_verdict_lets_the_policy_click(records, target, allowed):
    assert _policy_allows(origin_of(target, records)) is allowed


# -- Real windows (Windows only) --------------------------------------------------------------

WS_POPUP = 0x80000000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010


class _RealWindows:
    """Registers the Chromium class names in this process and creates real windows with them."""

    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
                                             ctypes.c_size_t, ctypes.c_ssize_t)
        self._proc = self._proc_type(lambda hwnd, msg, wparam, lparam: self.user32.DefWindowProcW(
            hwnd, msg, wparam, lparam))

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint), ("style", ctypes.c_uint), ("lpfnWndProc", self._proc_type),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p), ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p), ("lpszClassName", ctypes.c_wchar_p),
                ("hIconSm", ctypes.c_void_p),
            ]

        self._wndclass = WNDCLASSEXW
        self.user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        self.user32.DefWindowProcW.restype = ctypes.c_ssize_t
        self.kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        self.kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        self.user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        self.user32.RegisterClassExW.restype = ctypes.c_uint16
        self.user32.CreateWindowExW.argtypes = [
            ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]
        self.user32.CreateWindowExW.restype = ctypes.c_void_p
        self.user32.DestroyWindow.argtypes = [ctypes.c_void_p]
        self.user32.DestroyWindow.restype = ctypes.c_int
        self.user32.UnregisterClassW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
        self.user32.UnregisterClassW.restype = ctypes.c_int
        self._instance = self.kernel32.GetModuleHandleW(None)
        self._classes: list[str] = []
        self._windows: list[int] = []

    def register(self, class_name: str) -> None:
        wc = self._wndclass()
        wc.cbSize = ctypes.sizeof(self._wndclass)
        wc.lpfnWndProc = self._proc
        wc.hInstance = self._instance
        wc.lpszClassName = class_name
        self.user32.RegisterClassExW(ctypes.byref(wc))
        self._classes.append(class_name)

    def create(self, class_name: str, style: int, parent: int, x: int, y: int, width: int, height: int) -> int:
        if class_name not in self._classes:
            self.register(class_name)
        hwnd = self.user32.CreateWindowExW(0, class_name, None, style, x, y, width, height,
                                           parent or None, None, self._instance, None) or 0
        assert hwnd, f"CreateWindowExW failed for {class_name}: {ctypes.get_last_error()}"
        self._windows.append(hwnd)
        return hwnd

    def topmost(self, hwnd: int) -> None:
        """Keep the window above the others, so the point at its centre is not covered by a window on top."""
        self.user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                             ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        self.user32.SetWindowPos.restype = ctypes.c_bool
        self.user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def close(self) -> None:
        for hwnd in reversed(self._windows):
            self.user32.DestroyWindow(hwnd)
        for class_name in self._classes:
            self.user32.UnregisterClassW(class_name, self._instance)


@pytest.fixture
def own_windows(tmp_path, monkeypatch):
    """An unowned Chromium window and a popup it owns, both in this process and both visible.

    The working directory moves into the test's temp directory first. Creating windows loads the Windows
    input method into this process, and under the test runner's cleared environment that method writes its
    cache under a literal ``%SystemDrive%`` path relative to the working directory. That must not land in
    the repository.
    """
    monkeypatch.chdir(tmp_path)
    windows = _RealWindows()
    try:
        owner = windows.create(CHROMIUM, WS_POPUP | WS_VISIBLE, 0, 40, 40, 300, 200)
        popup = windows.create(CHROMIUM, WS_POPUP | WS_VISIBLE, owner, 60, 60, 200, 120)
        yield SimpleNamespace(windows=windows, owner=owner, popup=popup)
    finally:
        windows.close()


@pytest.mark.windows_only
def test_real_enumeration_reports_visible_window_with_its_class_pid_and_image(own_windows):
    record = snapshot_windows()[own_windows.owner]
    assert record.top_level and record.visible and record.parent == 0
    assert record.class_name == CHROMIUM
    assert record.pid == os.getpid()
    assert os.path.isabs(record.image_path) and record.image_path.lower().endswith(".exe")
    assert record.rect[2] - record.rect[0] == 300


@pytest.mark.windows_only
def test_real_owned_popup_is_native_until_a_renderer_surface_is_added(own_windows):
    records = snapshot_windows()
    owner_record = records[own_windows.owner]
    instance = BrowserInstance(pinned_exe=owner_record.image_path, pids=frozenset({os.getpid()}))
    assert records[own_windows.popup].parent == own_windows.owner  # GetParent reports the owner of an owned popup
    assert origin_of(own_windows.owner, records, instance) == NATIVE_UI_ORIGIN
    assert origin_of(own_windows.popup, records, instance) == NATIVE_UI_ORIGIN

    own_windows.windows.create(RENDERER_CLASS, WS_CHILD | WS_VISIBLE, own_windows.popup, 0, 0, 100, 50)
    after = snapshot_windows()
    assert origin_of(own_windows.popup, after, instance) == WEB_PAGE_ORIGIN
    assert origin_of(own_windows.owner, after, instance) == WEB_PAGE_ORIGIN


@pytest.mark.windows_only
def test_real_window_is_unknown_when_the_pinned_path_names_another_browser(own_windows):
    instance = BrowserInstance(pinned_exe=EDGE, pids=frozenset({os.getpid()}))
    assert origin_of(own_windows.popup, snapshot_windows(), instance) == UNKNOWN_ORIGIN


@pytest.mark.windows_only
def test_real_window_is_unknown_when_its_process_is_outside_the_instance(own_windows):
    records = snapshot_windows()
    instance = BrowserInstance(pinned_exe=records[own_windows.owner].image_path, pids=frozenset())
    assert origin_of(own_windows.popup, records, instance) == UNKNOWN_ORIGIN


# -- Click-time recheck ----------------------------------------------------------------------

def recheck(hit_hwnd, *, at_check, at_click=None, target=BUBBLE):
    """Recheck a click on ``target``. The verdict and the approved record come from the snapshot taken when the
    window passed the check. The window at the point is judged on the snapshot taken at click time."""
    verdict = classify_window(target, at_check, INSTANCE)
    return decide_click(verdict, at_check[target], hit_hwnd, at_check if at_click is None else at_click)


def test_the_same_window_at_the_point_passes():
    decision = recheck(BUBBLE, at_check=snapshot(top(MAIN), top(BUBBLE, owner=MAIN)))
    assert decision.allowed
    assert decision.approved_hwnd == BUBBLE and decision.hit_hwnd == BUBBLE


def test_a_different_hwnd_at_the_point_is_denied():
    records = snapshot(top(MAIN), top(BUBBLE, owner=MAIN), top(OTHER_DIALOG, owner=MAIN))
    decision = recheck(OTHER_DIALOG, at_check=records)
    assert not decision.allowed
    assert "different window" in decision.reason


def test_a_different_pid_for_the_same_hwnd_is_denied():
    """A window handle can be reused: the same hwnd, now created by another process."""
    at_check = snapshot(top(MAIN), top(BUBBLE, owner=MAIN))
    at_click = snapshot(top(MAIN), top(BUBBLE, owner=MAIN, pid=FOREIGN_PID, image=LOOKALIKE))
    decision = recheck(BUBBLE, at_check=at_check, at_click=at_click)
    assert not decision.allowed
    assert "another process" in decision.reason


def test_a_different_class_for_the_same_hwnd_is_denied():
    at_check = snapshot(top(MAIN), top(BUBBLE, owner=MAIN))
    at_click = snapshot(top(MAIN), top(BUBBLE, owner=MAIN, cls=FOREIGN_CLASS))
    decision = recheck(BUBBLE, at_check=at_check, at_click=at_click)
    assert not decision.allowed
    assert "another class" in decision.reason


def test_a_page_drawn_window_at_the_point_is_denied():
    """A page paints a window inside its renderer. Under the point it is page content, whatever it claims to be."""
    at_click = snapshot(top(MAIN), child(RENDERER, MAIN, cls=RENDERER_CLASS), child(FAKE, RENDERER, cls=CHROMIUM))
    decision = recheck(FAKE, at_check=snapshot(top(MAIN), top(BUBBLE, owner=MAIN)), at_click=at_click)
    assert not decision.allowed
    assert "page content" in decision.reason


def test_the_approved_hwnd_now_drawn_inside_a_renderer_is_denied():
    """The same hwnd, pid and class, but a renderer surface is now its ancestor."""
    at_click = snapshot(top(MAIN), child(RENDERER, MAIN, cls=RENDERER_CLASS), child(BUBBLE, RENDERER, cls=CHROMIUM))
    decision = recheck(BUBBLE, at_check=snapshot(top(MAIN), top(BUBBLE, owner=MAIN)), at_click=at_click)
    assert not decision.allowed
    assert "page content" in decision.reason


def test_a_window_that_starts_hosting_a_renderer_after_the_check_is_denied():
    at_click = snapshot(top(MAIN), top(BUBBLE, owner=MAIN), child(RENDERER, BUBBLE, cls=RENDERER_CLASS))
    decision = recheck(BUBBLE, at_check=snapshot(top(MAIN), top(BUBBLE, owner=MAIN)), at_click=at_click)
    assert not decision.allowed
    assert "page content" in decision.reason


def test_a_renderer_surface_at_the_point_is_denied():
    at_click = snapshot(top(MAIN), child(RENDERER, MAIN, cls=RENDERER_CLASS), top(BUBBLE, owner=MAIN))
    decision = recheck(RENDERER, at_check=snapshot(top(MAIN), top(BUBBLE, owner=MAIN)), at_click=at_click)
    assert not decision.allowed
    assert "renderer surface" in decision.reason


def test_no_window_at_the_point_is_denied():
    decision = recheck(0, at_check=snapshot(top(MAIN), top(BUBBLE, owner=MAIN)))
    assert not decision.allowed
    assert decision.reason == "no window at the target point"


def test_a_window_at_the_point_that_left_the_snapshot_is_denied():
    decision = recheck(0x999, at_check=snapshot(top(MAIN), top(BUBBLE, owner=MAIN)))
    assert not decision.allowed
    assert "not in the snapshot" in decision.reason


@pytest.mark.parametrize("origin", [WEB_PAGE_ORIGIN, UNKNOWN_ORIGIN])
def test_a_window_that_did_not_pass_native_ui_is_never_clickable(origin):
    records = snapshot(top(MAIN), top(BUBBLE, owner=MAIN))
    decision = decide_click(WindowVerdict(BUBBLE, origin, "not native"), records[BUBBLE], BUBBLE, records)
    assert not decision.allowed
    assert "native_ui" in decision.reason


def test_a_verdict_for_another_window_does_not_approve_this_one():
    records = snapshot(top(MAIN), top(BUBBLE, owner=MAIN))
    verdict_for_main = classify_window(MAIN, records, INSTANCE)
    decision = decide_click(verdict_for_main, records[BUBBLE], BUBBLE, records)
    assert not decision.allowed
    assert "native_ui" in decision.reason


@pytest.mark.windows_only
def test_real_recheck_allows_the_approved_window_at_its_centre(own_windows):
    own_windows.windows.topmost(own_windows.popup)
    records = snapshot_windows()
    popup = records[own_windows.popup]
    instance = BrowserInstance(pinned_exe=records[own_windows.owner].image_path, pids=frozenset({os.getpid()}))
    verdict = classify_window(own_windows.popup, records, instance)
    x, y = (popup.rect[0] + popup.rect[2]) // 2, (popup.rect[1] + popup.rect[3]) // 2
    decision = decide_click_at_point(verdict, popup, x, y)
    assert decision.allowed, decision.reason
    assert decision.hit_hwnd == own_windows.popup


@pytest.mark.windows_only
def test_real_recheck_denies_a_point_with_no_window_and_logs_the_reason(own_windows, caplog):
    records = snapshot_windows()
    popup = records[own_windows.popup]
    instance = BrowserInstance(pinned_exe=records[own_windows.owner].image_path, pids=frozenset({os.getpid()}))
    verdict = classify_window(own_windows.popup, records, instance)
    with caplog.at_level(logging.WARNING, logger="tools.browser_window_origin"):
        decision = decide_click_at_point(verdict, popup, -1_000_000, -1_000_000)
    assert not decision.allowed and decision.hit_hwnd == 0
    assert "no window at the target point" in caplog.text
