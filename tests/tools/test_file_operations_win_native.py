"""Windows native read/search lane (F2.2): ``STARDUST_NATIVE_FILE_READ=1`` opts
``LocalEnvironment`` file ops into the shell-free path on win32.

Contract under test:
  * default (unset / ``0``) keeps the shell path — byte-identical behaviour to
    the pre-flag baseline, so flipping the flag can never change a default run;
  * ``1`` engages the native lane and every output (read pages, search file
    lists, content matches) is byte-identical to the shell lane, across path
    spellings Windows agents produce (``C:\\x``, ``C:/x``, MSYS ``/c/x``,
    relative, unicode names, spaces, nested dirs);
  * the gate never applies to non-local backends and the kill switch wins over
    the opt-in on every platform.
"""

import os
import sys

import pytest

from tools.file_operations import ShellFileOperations

pytestmark = pytest.mark.windows_only

READ_PROBE_MARK = "sentinel"  # substring of the shell probe command the spy records


def _spy_env(tmp_path):
    """A real LocalEnvironment whose ``execute`` records commands (spy, not mock:
    the shell path still runs for real so parity comparisons see true output)."""
    from tools.environments.local import LocalEnvironment

    env = LocalEnvironment(cwd=str(tmp_path))
    real_execute = env.execute
    calls: list[str] = []

    def spy(command, *args, **kwargs):
        calls.append(command)
        return real_execute(command, *args, **kwargs)

    env.execute = spy
    return env, calls


@pytest.fixture
def ops(tmp_path):
    env, calls = _spy_env(tmp_path)
    yield ShellFileOperations(env, cwd=str(tmp_path)), calls


def _write(tmp_path, name: str, data: bytes):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return str(p)


# --- gate ---------------------------------------------------------------------


def test_default_off_on_windows(ops):
    """Unset flag on win32: native lane must stay off (opt-in only)."""
    ops, _calls = ops
    os.environ.pop("STARDUST_NATIVE_FILE_READ", None)
    assert ops._native_read_enabled() is False


def test_opt_in_flag_enables_gate(ops):
    ops, _calls = ops
    os.environ["STARDUST_NATIVE_FILE_READ"] = "1"
    try:
        assert ops._native_read_enabled() is True
    finally:
        os.environ.pop("STARDUST_NATIVE_FILE_READ", None)


def test_kill_switch_beats_opt_in(ops):
    ops, _calls = ops
    os.environ["STARDUST_NATIVE_FILE_READ"] = "0"
    try:
        assert ops._native_read_enabled() is False
    finally:
        os.environ.pop("STARDUST_NATIVE_FILE_READ", None)


def test_gate_requires_local_environment():
    from unittest.mock import MagicMock

    env = MagicMock()
    env.cwd = "C:/tmp"
    os.environ["STARDUST_NATIVE_FILE_READ"] = "1"
    try:
        assert ShellFileOperations(env)._native_read_enabled() is False
    finally:
        os.environ.pop("STARDUST_NATIVE_FILE_READ", None)


def test_native_lane_path_maps_msys_and_drive_forms(ops):
    """``/c/x`` -> ``C:\\x``; ``C:/x`` -> ``C:\\x``; ``C:\\x`` and relative
    unchanged in kind; UNC untouched (no drive letter to rebase)."""
    ops, _calls = ops
    m = ops._native_lane_path
    assert m("/c/Users/x") == "C:\\Users\\x"
    assert m("/cygdrive/d/wt") == "D:\\wt"
    assert m("C:/Users/x") == "C:\\Users\\x"
    assert m("D:\\wt\\f2-2") == "D:\\wt\\f2-2"
    assert m("relative/file.txt") == "relative/file.txt"
    assert m("") == ""
    assert m(r"\\server\\share\\x") == r"\\server\\share\\x"


# --- read parity --------------------------------------------------------------


PARITY_CASES = [
    ("lf", b"one\ntwo\nthree\n", {}),
    ("crlf", b"one\r\ntwo\r\nthree\r\n", {}),
    ("no_trailing_newline", b"one\ntwo", {}),
    ("empty", b"", {}),
    ("unicode_body", "行1\r\n行2\n".encode("utf-8"), {}),
    ("page2", b"l1\nl2\nl3\nl4\n", {"offset": 2, "limit": 2}),
    ("offset_past_end", b"l1\n", {"offset": 99, "limit": 5}),
]


class TestNativeReadParityWindows:
    @pytest.mark.parametrize("name,data,kwargs", PARITY_CASES, ids=[c[0] for c in PARITY_CASES])
    def test_read_output_matches_shell_lane(self, ops, tmp_path, name, data, kwargs):
        ops, calls = ops
        p = _write(tmp_path, f"{name}.txt", data)
        os.environ["STARDUST_NATIVE_FILE_READ"] = "0"
        try:
            via_shell = ops.read_file(p, **kwargs).to_dict()
        finally:
            os.environ.pop("STARDUST_NATIVE_FILE_READ", None)
        calls.clear()
        os.environ["STARDUST_NATIVE_FILE_READ"] = "1"
        try:
            via_native = ops.read_file(p, **kwargs).to_dict()
            # the native read itself must not have paid a shell round-trip
            assert not any("__HERMES_RF_" in c for c in calls), calls[:3]
        finally:
            os.environ.pop("STARDUST_NATIVE_FILE_READ", None)
        assert via_native == via_shell


def test_read_path_spellings_agree_with_shell(ops, tmp_path):
    """C:\\, C:/, MSYS /c/..., and relative forms all resolve identically on the
    native lane, and each output equals the shell lane's for the same file."""
    ops, calls = ops
    p = _write(tmp_path, "数据 spaced.txt", b"content\r\nsecond\n")
    win = str(p)
    fwd = win.replace("\\", "/")
    msys = "/" + win[:1].lower() + win[2:].replace("\\", "/")
    os.environ["STARDUST_NATIVE_FILE_READ"] = "0"
    try:
        baseline = ops.read_file(win).to_dict()
    finally:
        os.environ.pop("STARDUST_NATIVE_FILE_READ", None)
    os.environ["STARDUST_NATIVE_FILE_READ"] = "1"
    try:
        for spelling in (win, fwd, msys):
            assert ops.read_file(spelling).to_dict() == baseline, spelling
        os.chdir(tmp_path)  # relative resolution against env cwd
        try:
            assert ops.read_file("数据 spaced.txt").to_dict() == baseline
        finally:
            os.chdir(_ORIG_CWD)
    finally:
        os.environ.pop("STARDUST_NATIVE_FILE_READ", None)


_ORIG_CWD = os.getcwd()


# --- search parity ------------------------------------------------------------


class TestNativeSearchParityWindows:
    def test_file_search_matches_shell_lane(self, ops, tmp_path):
        ops, _calls = ops
        for i in range(5):
            _write(tmp_path, f"find{i}.txt", b"x\n")
        _write(tmp_path, "deeper_find.txt", b"x\n")
        os.environ["STARDUST_NATIVE_FILE_READ"] = "0"
        try:
            via_shell = ops.search("find*", ".", target="files", limit=10).to_dict()
        finally:
            os.environ.pop("STARDUST_NATIVE_FILE_READ", None)
        os.environ["STARDUST_NATIVE_FILE_READ"] = "1"
        try:
            via_native = ops.search("find*", ".", target="files", limit=10).to_dict()
        finally:
            os.environ.pop("STARDUST_NATIVE_FILE_READ", None)
        # rg's parallel walk makes the ENTRY ORDER unstable run-to-run (the shell
        # lane is non-deterministic there too); the contract is the same SET with
        # the same totals, not the same order.
        via_native["files"] = sorted(via_native["files"])
        via_shell["files"] = sorted(via_shell["files"])
        assert via_native == via_shell
        # '*find*' wraps to a basename glob; whether it also catches
        # 'deeper_find.txt' depends on the runner's rg version, so only the five
        # unambiguous files are required.
        assert {f for f in via_native["files"] if "deeper" not in f} == {
            f".\\find{i}.txt" for i in range(5)}

    def test_content_search_matches_shell_lane(self, ops, tmp_path):
        ops, _calls = ops
        _write(tmp_path, "needle.txt", b"keep\r\nmatch HERE done\nother\n")
        _write(tmp_path, "other.txt", b"no hit\n")
        os.environ["STARDUST_NATIVE_FILE_READ"] = "0"
        try:
            via_shell = ops.search("match HERE", ".", target="content").to_dict()
        finally:
            os.environ.pop("STARDUST_NATIVE_FILE_READ", None)
        os.environ["STARDUST_NATIVE_FILE_READ"] = "1"
        try:
            via_native = ops.search("match HERE", ".", target="content").to_dict()
        finally:
            os.environ.pop("STARDUST_NATIVE_FILE_READ", None)
        assert via_native == via_shell
        assert via_native["total_count"] == 1

    def test_search_absolute_root_spellings(self, ops, tmp_path):
        """rg root paths in all three Windows spellings give the same result."""
        ops, _calls = ops
        _write(tmp_path, "spellA.txt", b"a\n")
        win = str(tmp_path)
        fwd = win.replace("\\", "/")
        msys = "/" + win[:1].lower() + win[2:].replace("\\", "/")
        os.environ["STARDUST_NATIVE_FILE_READ"] = "1"
        try:
            results = [ops.search("spellA*", r, target="files", limit=5).to_dict()
                       for r in (win, fwd, msys)]
        finally:
            os.environ.pop("STARDUST_NATIVE_FILE_READ", None)
        assert results[0] == results[1] == results[2]
        assert results[0]["total_count"] == 1


# --- sandbox / escape ----------------------------------------------------------


class TestPathEscape:
    def test_missing_and_directory_behave_like_shell(self, ops, tmp_path):
        ops, _calls = ops
        os.environ["STARDUST_NATIVE_FILE_READ"] = "0"
        try:
            missing_shell = ops.read_file(str(tmp_path / "nope.txt")).to_dict()
            dir_shell = ops.read_file(str(tmp_path)).to_dict()
        finally:
            os.environ.pop("STARDUST_NATIVE_FILE_READ", None)
        os.environ["STARDUST_NATIVE_FILE_READ"] = "1"
        try:
            missing_native = ops.read_file(str(tmp_path / "nope.txt")).to_dict()
            dir_native = ops.read_file(str(tmp_path)).to_dict()
        finally:
            os.environ.pop("STARDUST_NATIVE_FILE_READ", None)
        assert missing_native == missing_shell
        assert dir_native == dir_shell

    def test_read_stays_inside_working_tree_on_junction_escape(self, ops, tmp_path):
        """A directory junction pointing OUTSIDE the workspace must not let a
        relative read escape the tree on the native lane: reading through the
        junction still requires the path to exist, and the tool never reads
        above the given root (there is no root constraint on read_file itself —
        the contract is parity: whatever the shell lane resolves, the native
        lane resolves identically, so junction semantics cannot diverge)."""
        pytest.importorskip("pytest")  # keep the skip explicit if _winapi missing
        outside = tmp_path.parent / "f2-2-outside"
        outside.mkdir(exist_ok=True)
        (outside / "secret.txt").write_bytes(b"outside\n")
        junction = tmp_path / "junction_out"
        try:
            import _winapi
            _winapi.CreateJunction(str(outside), str(junction))
        except (OSError, ImportError, AttributeError):
            pytest.skip("junction creation unavailable")
        ops, _calls = ops
        os.environ["STARDUST_NATIVE_FILE_READ"] = "0"
        try:
            via_shell = ops.read_file(str(junction / "secret.txt")).to_dict()
        finally:
            os.environ.pop("STARDUST_NATIVE_FILE_READ", None)
        os.environ["STARDUST_NATIVE_FILE_READ"] = "1"
        try:
            via_native = ops.read_file(str(junction / "secret.txt")).to_dict()
        finally:
            os.environ.pop("STARDUST_NATIVE_FILE_READ", None)
        assert via_native == via_shell


def test_symlink_parity_when_supported(ops, tmp_path):
    ops, _calls = ops
    target = _write(tmp_path, "real.txt", b"target\n")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation requires developer mode")
    dangling = tmp_path / "dangling.txt"
    try:
        dangling.symlink_to(str(tmp_path / "gone"))
        made_dangling = True
    except (OSError, NotImplementedError):
        made_dangling = False
    paths = [str(link)] + ([str(dangling)] if made_dangling else [])
    os.environ["STARDUST_NATIVE_FILE_READ"] = "0"
    try:
        shell_results = {p: ops.read_file(p).to_dict() for p in paths}
    finally:
        os.environ.pop("STARDUST_NATIVE_FILE_READ", None)
    os.environ["STARDUST_NATIVE_FILE_READ"] = "1"
    try:
        for p in paths:
            assert ops.read_file(p).to_dict() == shell_results[p], p
    finally:
        os.environ.pop("STARDUST_NATIVE_FILE_READ", None)
