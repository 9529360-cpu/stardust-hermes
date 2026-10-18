"""Release publishing must not commit unrelated index state or publish after a failed push."""

from __future__ import annotations

import http.server
import importlib.util
import os
import runpy
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release.py"
spec = importlib.util.spec_from_file_location("release_publish_safety", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _new_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release-test@example.invalid")

    version_file = repo / "hermes_cli" / "__init__.py"
    version_file.parent.mkdir()
    version_file.write_text(
        '__version__ = "1.2.3"\n__release_date__ = "2026.1.1"\n',
        encoding="utf-8",
    )
    pyproject_file = repo / "pyproject.toml"
    pyproject_file.write_text('version = "1.2.3"\n', encoding="utf-8")
    _git(repo, "add", "hermes_cli/__init__.py", "pyproject.toml")
    _git(repo, "commit", "-m", "initial version")
    return repo, version_file, pyproject_file


def _start_http_git_origin(project_root: Path):
    class GitBackendHandler(http.server.BaseHTTPRequestHandler):
        timeout = 10

        def _serve_backend(self):
            request_body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            query = urlsplit(self.path)
            env = os.environ.copy()
            env.update(
                {
                    "GIT_PROJECT_ROOT": str(project_root),
                    "GIT_HTTP_EXPORT_ALL": "1",
                    "PATH_INFO": query.path,
                    "QUERY_STRING": query.query,
                    "REQUEST_METHOD": self.command,
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "CONTENT_LENGTH": str(len(request_body)),
                    "REMOTE_ADDR": self.client_address[0],
                }
            )
            if protocol := self.headers.get("Git-Protocol"):
                env["HTTP_GIT_PROTOCOL"] = protocol
            try:
                result = subprocess.run(
                    ["git", "http-backend"],
                    input=request_body,
                    capture_output=True,
                    env=env,
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                self.send_error(504, "Git backend timed out")
                return
            headers, separator, body = result.stdout.partition(b"\r\n\r\n")
            if not separator:
                headers, separator, body = result.stdout.partition(b"\n\n")
            if result.returncode or not separator:
                self.send_error(500, result.stderr.decode(errors="replace"))
                return

            status = 200
            response_headers = []
            for line in headers.splitlines():
                name, _, value = line.decode("latin-1").partition(":")
                if name.lower() == "status":
                    status = int(value.strip().split(" ", 1)[0])
                elif name:
                    response_headers.append((name, value.strip()))
            self.send_response(status)
            for name, value in response_headers:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        do_GET = _serve_backend
        do_POST = _serve_backend

        def log_message(self, format, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), GitBackendHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _configure_first_release(monkeypatch, repo, version_file, pyproject_file, *extra_args):
    monkeypatch.setattr(release, "REPO_ROOT", repo)
    monkeypatch.setattr(release, "VERSION_FILE", version_file)
    monkeypatch.setattr(release, "PYPROJECT_FILE", pyproject_file)
    monkeypatch.setattr(release, "get_commits", lambda since_tag: [])
    monkeypatch.setattr(
        release, "generate_changelog", lambda *args, **kwargs: "# Release notes\n"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release.py",
            "--publish",
            "--first-release",
            "--date",
            "2026.10.14",
            *extra_args,
        ],
    )


def _guard_external_processes(
    monkeypatch, *, gh_returncode: int | None = None, gh_stdout: str = "", gh_stderr: str = ""
):
    real_run = subprocess.run
    spawned_commands = []

    def guard(command, *args, **kwargs):
        if command and command[0] == "gh":
            spawned_commands.append(command)
            if gh_returncode is None:
                raise AssertionError("release.py unexpectedly invoked gh")
            return subprocess.CompletedProcess(
                command, gh_returncode, stdout=gh_stdout, stderr=gh_stderr
            )
        if command[:2] == ["git", "push"]:
            spawned_commands.append(command)
            raise AssertionError("release.py attempted a real git push")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(release.subprocess, "run", guard)
    return spawned_commands


def test_publish_bump_refuses_preexisting_staged_changes_before_mutation(
    tmp_path, monkeypatch, capsys
):
    repo, version_file, pyproject_file = _new_repo(tmp_path)
    original_version = version_file.read_text(encoding="utf-8")
    before_head = _git(repo, "rev-parse", "HEAD")
    unrelated = repo / "unrelated.txt"
    unrelated.write_text("keep this staged file\n", encoding="utf-8")
    _git(repo, "add", "unrelated.txt")

    monkeypatch.setattr(release, "REPO_ROOT", repo)
    monkeypatch.setattr(release, "VERSION_FILE", version_file)
    monkeypatch.setattr(release, "PYPROJECT_FILE", pyproject_file)
    monkeypatch.setattr(
        sys,
        "argv",
        ["release.py", "--publish", "--bump", "patch", "--date", "2026.10.14"],
    )
    commands = []
    run_git_result = release.git_result

    def track_git_result(*args, **kwargs):
        commands.append(args)
        return run_git_result(*args, **kwargs)

    monkeypatch.setattr(release, "git_result", track_git_result)
    gh_checks = []
    monkeypatch.setattr(
        release.shutil, "which", lambda command: gh_checks.append(command) or "fake-gh"
    )
    spawned_commands = _guard_external_processes(monkeypatch)

    status = release.main()

    output = capsys.readouterr().out
    assert status == 1
    assert "staged changes" in output
    assert "Commit or stash" in output
    assert version_file.read_text(encoding="utf-8") == original_version
    assert _git(repo, "rev-parse", "HEAD") == before_head
    assert _git(repo, "tag", "--list") == ""
    assert _git(repo, "diff", "--cached", "--name-only") == "unrelated.txt"
    assert commands == [("diff", "--cached", "--quiet")]
    assert gh_checks == []
    assert spawned_commands == []
    assert not (repo / ".release_notes.md").exists()


def test_index_inspection_failure_returns_nonzero_before_mutation(
    tmp_path, monkeypatch, capsys
):
    repo, version_file, pyproject_file = _new_repo(tmp_path)
    original_version = version_file.read_text(encoding="utf-8")
    original_pyproject = pyproject_file.read_text(encoding="utf-8")
    before_head = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(release, "REPO_ROOT", repo)
    monkeypatch.setattr(release, "VERSION_FILE", version_file)
    monkeypatch.setattr(release, "PYPROJECT_FILE", pyproject_file)
    monkeypatch.setattr(
        sys,
        "argv",
        ["release.py", "--publish", "--bump", "patch", "--date", "2026.10.14"],
    )
    commands = []

    def fail_index_inspection(*args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(
            ["git", *args], 128, stdout="", stderr="index unavailable"
        )

    monkeypatch.setattr(release, "git_result", fail_index_inspection)

    status = release.main()

    error = capsys.readouterr().err
    assert status == 1
    assert "Cannot safely inspect the Git index: index unavailable" in error
    assert version_file.read_text(encoding="utf-8") == original_version
    assert pyproject_file.read_text(encoding="utf-8") == original_pyproject
    assert _git(repo, "rev-parse", "HEAD") == before_head
    assert _git(repo, "tag", "--list") == ""
    assert commands == [("diff", "--cached", "--quiet")]


def test_release_version_file_inspection_failure_returns_nonzero(
    tmp_path, monkeypatch, capsys
):
    repo, version_file, pyproject_file = _new_repo(tmp_path)
    original_version = version_file.read_text(encoding="utf-8")
    before_head = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(release, "REPO_ROOT", repo)
    monkeypatch.setattr(release, "VERSION_FILE", version_file)
    monkeypatch.setattr(release, "PYPROJECT_FILE", pyproject_file)
    monkeypatch.setattr(
        sys,
        "argv",
        ["release.py", "--publish", "--bump", "patch", "--date", "2026.10.14"],
    )
    commands = []

    def fail_target_inspection(*args, **kwargs):
        commands.append(args)
        if args[0] == "diff":
            return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            ["git", *args], 128, stdout="", stderr="worktree unavailable"
        )

    monkeypatch.setattr(release, "git_result", fail_target_inspection)

    status = release.main()

    error = capsys.readouterr().err
    assert status == 1
    assert "Cannot safely inspect release version files: worktree unavailable" in error
    assert version_file.read_text(encoding="utf-8") == original_version
    assert _git(repo, "rev-parse", "HEAD") == before_head
    assert _git(repo, "tag", "--list") == ""
    assert commands == [
        ("diff", "--cached", "--quiet"),
        (
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            "hermes_cli/__init__.py",
            "pyproject.toml",
        ),
    ]


def test_script_entry_propagates_nonzero_preflight_status(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "--publish", "--bump", "patch", "--date", "2026.10.14"],
    )

    def find_staged_changes(command, **kwargs):
        assert command == ["git", "diff", "--cached", "--quiet"]
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", find_staged_changes)

    try:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("release.py did not propagate the preflight failure status")


def test_unstaged_release_version_edit_is_preserved_and_blocks_publish(
    tmp_path, monkeypatch, capsys
):
    repo, version_file, pyproject_file = _new_repo(tmp_path)
    version_file.write_text(
        version_file.read_text(encoding="utf-8") + "# operator edit\n",
        encoding="utf-8",
    )
    edited_version = version_file.read_text(encoding="utf-8")
    original_pyproject = pyproject_file.read_text(encoding="utf-8")
    unrelated = repo / "unrelated.txt"
    unrelated.write_text("keep this unstaged file\n", encoding="utf-8")
    before_head = _git(repo, "rev-parse", "HEAD")

    monkeypatch.setattr(release, "REPO_ROOT", repo)
    monkeypatch.setattr(release, "VERSION_FILE", version_file)
    monkeypatch.setattr(release, "PYPROJECT_FILE", pyproject_file)
    monkeypatch.setattr(
        sys,
        "argv",
        ["release.py", "--publish", "--bump", "patch", "--date", "2026.10.14"],
    )
    commands = []
    run_git_result = release.git_result

    def track_git_result(*args, **kwargs):
        commands.append(args)
        return run_git_result(*args, **kwargs)

    monkeypatch.setattr(release, "git_result", track_git_result)
    gh_checks = []
    monkeypatch.setattr(
        release.shutil, "which", lambda command: gh_checks.append(command) or "fake-gh"
    )
    spawned_commands = _guard_external_processes(monkeypatch)

    status = release.main()

    output = capsys.readouterr().out
    assert status == 1
    assert "release version files have unstaged changes" in output
    assert "hermes_cli/__init__.py" in output
    assert version_file.read_text(encoding="utf-8") == edited_version
    assert pyproject_file.read_text(encoding="utf-8") == original_pyproject
    assert unrelated.read_text(encoding="utf-8") == "keep this unstaged file\n"
    assert _git(repo, "rev-parse", "HEAD") == before_head
    assert _git(repo, "tag", "--list") == ""
    assert _git(repo, "diff", "--cached", "--name-only") == ""
    assert commands == [
        ("diff", "--cached", "--quiet"),
        (
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            "hermes_cli/__init__.py",
            "pyproject.toml",
        ),
    ]
    assert gh_checks == []
    assert spawned_commands == []
    assert not (repo / ".release_notes.md").exists()


def test_push_failure_keeps_local_tag_and_notes_without_attempting_gh(
    tmp_path, monkeypatch, capsys
):
    repo, version_file, pyproject_file = _new_repo(tmp_path)
    unrelated = repo / "unrelated.txt"
    unrelated.write_text("leave this unstaged\n", encoding="utf-8")
    monkeypatch.setattr(release, "REPO_ROOT", repo)
    monkeypatch.setattr(release, "VERSION_FILE", version_file)
    monkeypatch.setattr(release, "PYPROJECT_FILE", pyproject_file)
    monkeypatch.setattr(release, "get_commits", lambda since_tag: [])
    notes = "# Stardust release notes\n"
    monkeypatch.setattr(release, "generate_changelog", lambda *args, **kwargs: notes)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release.py",
            "--publish",
            "--bump",
            "patch",
            "--first-release",
            "--date",
            "2026.10.14",
        ],
    )

    git_commands = []
    run_git_result = release.git_result

    def fail_push(*args, **kwargs):
        git_commands.append(args)
        if args[0] == "push":
            return subprocess.CompletedProcess(
                ["git", *args], 1, stdout="", stderr="remote rejected"
            )
        return run_git_result(*args, **kwargs)

    monkeypatch.setattr(release, "git_result", fail_push)

    real_run = subprocess.run
    spawned_commands = []

    def refuse_gh(command, *args, **kwargs):
        spawned_commands.append(command)
        if command and command[0] == "gh":
            raise AssertionError("release.py attempted to invoke gh after push failure")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(release.subprocess, "run", refuse_gh)
    monkeypatch.setattr(release.shutil, "which", lambda command: "fake-gh")

    status = release.main()

    output = capsys.readouterr().out
    tag_name = "v2026.10.14"
    assert status == 1
    assert _git(repo, "tag", "--list", tag_name) == tag_name
    assert _git(repo, "show", "--pretty=format:", "--name-only", "HEAD").splitlines() == [
        "hermes_cli/__init__.py",
        "pyproject.toml",
    ]
    assert "?? unrelated.txt" in _git(repo, "status", "--porcelain")
    assert unrelated.read_text(encoding="utf-8") == "leave this unstaged\n"
    assert (repo / ".release_notes.md").read_text(encoding="utf-8") == notes
    assert git_commands == [
        ("diff", "--cached", "--quiet"),
        (
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            "hermes_cli/__init__.py",
            "pyproject.toml",
        ),
        ("add", str(version_file), str(pyproject_file)),
        ("commit", "-m", "chore: bump version to v1.2.4 (2026.10.14)"),
        ("tag", "-a", tag_name, "-m", "Stardust v1.2.4 (2026.10.14)\n\nStardust release"),
        (
            "push",
            "--no-follow-tags",
            "origin",
            "HEAD",
            f"refs/tags/{tag_name}:refs/tags/{tag_name}",
        ),
    ]
    assert not any(command and command[0] == "gh" for command in spawned_commands)
    assert "remote rejected" in output
    assert f"Local tag {tag_name} and release notes were preserved." in output
    assert f"git push --no-follow-tags origin HEAD refs/tags/{tag_name}:refs/tags/{tag_name}" in output
    assert f"gh release create {tag_name}" in output


@pytest.mark.parametrize(
    ("failure_command", "bump", "expected_commands", "failure_message"),
    [
        ("add", True, ["diff", "status", "add"], "Failed to stage version files"),
        (
            "commit",
            True,
            ["diff", "status", "add", "commit"],
            "Failed to commit version bump",
        ),
        ("tag", False, ["tag"], "Failed to create tag"),
    ],
)
def test_git_publish_failures_return_nonzero_without_remote_actions(
    tmp_path,
    monkeypatch,
    capsys,
    failure_command,
    bump,
    expected_commands,
    failure_message,
):
    repo, version_file, pyproject_file = _new_repo(tmp_path)
    before_head = _git(repo, "rev-parse", "HEAD")
    extra_args = ("--bump", "patch") if bump else ()
    _configure_first_release(monkeypatch, repo, version_file, pyproject_file, *extra_args)

    git_commands = []
    run_git_result = release.git_result

    def fail_git_step(*args, **kwargs):
        git_commands.append(args)
        if args[0] == failure_command:
            return subprocess.CompletedProcess(
                ["git", *args], 1, stdout="", stderr="simulated git failure"
            )
        if args[0] == "push":
            raise AssertionError("release.py reached push after an earlier git failure")
        return run_git_result(*args, **kwargs)

    monkeypatch.setattr(release, "git_result", fail_git_step)
    gh_checks = []
    monkeypatch.setattr(
        release.shutil, "which", lambda command: gh_checks.append(command) or "fake-gh"
    )
    spawned_commands = _guard_external_processes(monkeypatch)

    status = release.main()

    output = capsys.readouterr().out
    assert status == 1
    assert failure_message in output
    assert "simulated git failure" in output
    assert [command[0] for command in git_commands] == expected_commands
    assert _git(repo, "rev-parse", "HEAD") == before_head
    assert _git(repo, "tag", "--list") == ""
    assert gh_checks == []
    assert spawned_commands == []
    assert not (repo / ".release_notes.md").exists()
    assert not any(command[0] == "push" for command in git_commands)


def test_missing_gh_returns_nonzero_and_keeps_recovery_notes(
    tmp_path, monkeypatch, capsys
):
    repo, version_file, pyproject_file = _new_repo(tmp_path)
    _configure_first_release(monkeypatch, repo, version_file, pyproject_file)

    git_commands = []
    run_git_result = release.git_result

    def fake_push(*args, **kwargs):
        git_commands.append(args)
        if args[0] == "push":
            return subprocess.CompletedProcess(
                ["git", *args], 0, stdout="", stderr=""
            )
        return run_git_result(*args, **kwargs)

    monkeypatch.setattr(release, "git_result", fake_push)
    gh_checks = []
    monkeypatch.setattr(
        release.shutil, "which", lambda command: gh_checks.append(command) or None
    )
    spawned_commands = _guard_external_processes(monkeypatch)

    status = release.main()

    output = capsys.readouterr().out
    tag_name = "v2026.10.14"
    assert status == 1
    assert "GitHub release skipped: `gh` CLI not found" in output
    assert _git(repo, "tag", "--list", tag_name) == tag_name
    assert (repo / ".release_notes.md").read_text(encoding="utf-8") == "# Release notes\n"
    assert [command[0] for command in git_commands] == ["tag", "push"]
    assert gh_checks == ["gh"]
    assert spawned_commands == []


def test_failed_gh_release_returns_nonzero_and_keeps_recovery_notes(
    tmp_path, monkeypatch, capsys
):
    repo, version_file, pyproject_file = _new_repo(tmp_path)
    _configure_first_release(monkeypatch, repo, version_file, pyproject_file)

    git_commands = []
    run_git_result = release.git_result

    def fake_push(*args, **kwargs):
        git_commands.append(args)
        if args[0] == "push":
            return subprocess.CompletedProcess(
                ["git", *args], 0, stdout="", stderr=""
            )
        return run_git_result(*args, **kwargs)

    monkeypatch.setattr(release, "git_result", fake_push)
    gh_checks = []
    monkeypatch.setattr(
        release.shutil, "which", lambda command: gh_checks.append(command) or "fake-gh"
    )
    spawned_commands = _guard_external_processes(
        monkeypatch, gh_returncode=1, gh_stderr="simulated release failure"
    )

    status = release.main()

    output = capsys.readouterr().out
    tag_name = "v2026.10.14"
    assert status == 1
    assert "GitHub release failed: simulated release failure" in output
    assert _git(repo, "tag", "--list", tag_name) == tag_name
    assert (repo / ".release_notes.md").read_text(encoding="utf-8") == "# Release notes\n"
    assert [command[0] for command in git_commands] == ["tag", "push"]
    assert gh_checks == ["gh"]
    assert len(spawned_commands) == 1
    assert spawned_commands[0][:4] == ["gh", "release", "create", tag_name]


def test_successful_publish_and_dry_run_keep_legacy_success_status(
    tmp_path, monkeypatch, capsys
):
    repo, version_file, pyproject_file = _new_repo(tmp_path)
    _configure_first_release(monkeypatch, repo, version_file, pyproject_file)

    run_git_result = release.git_result

    def successful_push(*args, **kwargs):
        if args[0] == "push":
            return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")
        return run_git_result(*args, **kwargs)

    monkeypatch.setattr(release, "git_result", successful_push)
    monkeypatch.setattr(release.shutil, "which", lambda command: "fake-gh")
    spawned_commands = _guard_external_processes(
        monkeypatch, gh_returncode=0, gh_stdout="published"
    )

    assert release.main() is None
    capsys.readouterr()
    assert _git(repo, "tag", "--list", "v2026.10.14") == "v2026.10.14"
    assert not (repo / ".release_notes.md").exists()
    assert len(spawned_commands) == 1

    monkeypatch.setattr(
        sys,
        "argv",
        ["release.py", "--first-release", "--date", "2026.10.15"],
    )
    monkeypatch.setattr(release, "git_result", lambda *args, **kwargs: pytest.fail(args))
    assert release.main() is None
    assert "Dry run complete" in capsys.readouterr().out


def test_publish_without_previous_tag_requires_first_release_but_preview_is_noop(
    tmp_path, monkeypatch, capsys
):
    repo, version_file, pyproject_file = _new_repo(tmp_path)
    original_version = version_file.read_text(encoding="utf-8")
    original_pyproject = pyproject_file.read_text(encoding="utf-8")
    before_head = _git(repo, "rev-parse", "HEAD")
    _configure_first_release(monkeypatch, repo, version_file, pyproject_file)
    monkeypatch.setattr(
        sys,
        "argv",
        ["release.py", "--publish", "--bump", "patch", "--date", "2026.10.14"],
    )
    spawned_commands = _guard_external_processes(monkeypatch)

    status = release.main()

    output = capsys.readouterr().out
    assert status == 1
    assert "No previous tags found" in output
    assert "Use --first-release" in output
    assert version_file.read_text(encoding="utf-8") == original_version
    assert pyproject_file.read_text(encoding="utf-8") == original_pyproject
    assert _git(repo, "rev-parse", "HEAD") == before_head
    assert _git(repo, "tag", "--list") == ""
    assert not (repo / ".release_notes.md").exists()
    assert spawned_commands == []

    monkeypatch.setattr(
        sys,
        "argv",
        ["release.py", "--bump", "patch", "--date", "2026.10.14"],
    )
    assert release.main() is None
    preview = capsys.readouterr().out
    assert "No previous tags found" in preview
    assert "Would create tag: v2026.10.14" in preview
    assert _git(repo, "tag", "--list") == ""
    assert spawned_commands == []


@pytest.mark.parametrize("same_named_branch", [False, True])
def test_publish_pushes_only_final_tag_and_branch_with_follow_tags_enabled(
    tmp_path, monkeypatch, capsys, same_named_branch
):
    repo, version_file, pyproject_file = _new_repo(tmp_path)
    tag_name = "v2026.10.14.3"
    history_tags = ["legacy-lightweight", "v2026.9.1", "v2026.10.14", "v2026.10.14.2"]
    _git(repo, "tag", history_tags[0])
    _git(repo, "tag", "-a", history_tags[1], "-m", "historical annotated release")
    for history_tag in history_tags[2:]:
        _git(repo, "tag", history_tag)
    branch_name = tag_name if same_named_branch else "release-target"
    _git(repo, "branch", "-m", branch_name)
    _git(repo, "config", "push.followTags", "true")

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(origin)], check=True)
    _git(origin, "config", "http.receivepack", "true")

    _configure_first_release(monkeypatch, repo, version_file, pyproject_file)
    real_run = subprocess.run
    gh_commands = []

    def intercept_gh(command, *args, **kwargs):
        if command[:3] == ["gh", "release", "create"]:
            gh_commands.append(command)
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr="GitHub release intentionally withheld"
            )
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(release.subprocess, "run", intercept_gh)
    monkeypatch.setattr(release.shutil, "which", lambda command: "fake-gh")

    server, server_thread = _start_http_git_origin(tmp_path)
    try:
        _git(
            repo,
            "remote",
            "add",
            "origin",
            f"http://127.0.0.1:{server.server_address[1]}/origin.git",
        )
        status = release.main()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    output = capsys.readouterr().out
    remote_refs = _git(
        origin,
        "for-each-ref",
        "--format=%(refname)",
    ).splitlines()
    assert status == 1
    assert remote_refs == [f"refs/heads/{branch_name}", f"refs/tags/{tag_name}"], output
    assert _git(repo, "tag", "--list", "--sort=refname") == "\n".join(
        sorted([*history_tags, tag_name])
    )
    expected_head = _git(repo, "rev-parse", "HEAD")
    assert _git(origin, "rev-parse", f"refs/heads/{branch_name}") == expected_head
    assert _git(origin, "rev-parse", f"refs/tags/{tag_name}^{{}}") == expected_head
    assert _git(origin, "rev-parse", f"refs/tags/{tag_name}") == _git(
        repo, "rev-parse", f"refs/tags/{tag_name}"
    )
    assert len(gh_commands) == 1
    assert gh_commands[0][3] == tag_name
    assert "GitHub release intentionally withheld" in output
