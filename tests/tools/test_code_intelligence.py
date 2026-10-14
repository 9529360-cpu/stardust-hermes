import json
import subprocess

import pytest
import tools.code_intelligence  # noqa: F401 - registers the tool for real registry dispatch
from agent.lsp.client import file_uri
from tools.registry import registry


def test_registry_dispatch_previews_code_actions_without_applying_or_leaving_workspace(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    source = repo / "sample.py"
    original = "count = 1\n"
    source.write_text(original)
    outside = tmp_path / "outside.py"
    outside.write_text("outside = 1\n")
    monkeypatch.chdir(repo)

    class FakeService:
        def is_active(self):
            return True

        def semantic_query_sync(self, *args, **kwargs):
            return {"available": True, "result": [
                {
                    "title": "Rename count to total",
                    "kind": "refactor.rewrite",
                    "isPreferred": True,
                    "edit": {"changes": {file_uri(str(source)): [{
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 5},
                        },
                        "newText": "total",
                    }]}},
                },
                {
                    "title": "Edit outside the workspace",
                    "edit": {"changes": {file_uri(str(outside)): [{
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 7},
                        },
                        "newText": "changed",
                    }]}},
                },
                {
                    "title": "Run server command",
                    "command": {"title": "Run server command", "command": "test.execute"},
                },
            ]}

    monkeypatch.setattr("agent.lsp.get_service", lambda: FakeService())
    monkeypatch.setattr("tools.file_tools_paths._uses_container_paths", lambda task_id: False)
    monkeypatch.setattr("tools.file_tools_paths._resolve_path_for_task", lambda path, task_id: repo / path)

    result = json.loads(registry.dispatch("code_intelligence", {
        "action": "code_actions", "path": "sample.py", "line": 1, "character": 1,
    }, task_id="task-123"))

    assert result["available"] is True
    assert result["actions"][0]["preferred"] is True
    assert result["actions"][0]["files_changed"] == 1
    assert "*** Begin Patch" in result["actions"][0]["diff_preview"]
    assert f"*** Update File: {source}" in result["actions"][0]["diff_preview"]
    assert "-count = 1" in result["actions"][0]["diff_preview"]
    assert "+total = 1" in result["actions"][0]["diff_preview"]
    assert "outside the active Git workspace" in result["actions"][1]["preview_error"]
    assert result["actions"][2]["command_not_run"] is True
    assert source.read_text() == original


def test_registry_dispatch_applies_selected_code_action_through_guarded_patch(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    source = repo / "sample.py"
    original = "count = 1\n"
    source.write_text(original)
    monkeypatch.chdir(repo)

    class FakeService:
        def __init__(self):
            self.query_action = None

        def is_active(self):
            return True

        def semantic_query_sync(self, path, action, **kwargs):
            self.query_action = action
            assert kwargs["line"] == 0
            return {"available": True, "result": [{
                "title": "Rename count to total",
                "kind": "quickfix",
                "edit": {"changes": {file_uri(str(source)): [{
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 5},
                    },
                    "newText": "total",
                }] }},
            }]}

    service = FakeService()
    monkeypatch.setattr("agent.lsp.get_service", lambda: service)
    monkeypatch.setattr("tools.file_tools_paths._uses_container_paths", lambda task_id: False)
    monkeypatch.setattr("tools.file_tools_paths._resolve_path_for_task", lambda path, task_id: repo / path)
    patch_calls = []

    def guarded_patch(**kwargs):
        patch_calls.append(kwargs)
        return json.dumps({"files_modified": [str(source)], "lint": "clean", "lsp_diagnostics": "none"})

    monkeypatch.setattr("tools.file_tools.patch_tool", guarded_patch)
    result = json.loads(registry.dispatch("code_intelligence", {
        "action": "apply_code_action", "path": "sample.py", "line": 1, "character": 1,
        "action_title": "Rename count to total", "action_index": 0,
    }, task_id="task-123", session_id="session-456"))

    assert service.query_action == "code_actions"
    assert result == {
        "available": True, "action": "apply_code_action", "title": "Rename count to total", "action_index": 0, "applied": True,
        "files_changed": 1, "files_modified": [str(source)], "lint": "clean", "lsp_diagnostics": "none",
        "warning": None,
    }
    assert len(patch_calls) == 1
    assert patch_calls[0]["mode"] == "patch"
    assert patch_calls[0]["task_id"] == "task-123"
    assert patch_calls[0]["session_id"] == "session-456"
    assert f"*** Update File: {source}" in patch_calls[0]["patch"]
    assert "-count = 1" in patch_calls[0]["patch"]
    assert "+total = 1" in patch_calls[0]["patch"]
    assert source.read_text() == original


def test_apply_code_action_rejects_server_commands_even_when_edit_is_present(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    source = repo / "sample.py"
    source.write_text("count = 1\n")
    monkeypatch.chdir(repo)

    class FakeService:
        def is_active(self):
            return True

        def semantic_query_sync(self, *args, **kwargs):
            return {"available": True, "result": [{
                "title": "Apply and run command",
                "edit": {"changes": {file_uri(str(source)): [{
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 5},
                    },
                    "newText": "total",
                }] }},
                "command": {"title": "Run command", "command": "server.execute"},
            }]}

    monkeypatch.setattr("agent.lsp.get_service", lambda: FakeService())
    monkeypatch.setattr("tools.file_tools_paths._uses_container_paths", lambda task_id: False)
    monkeypatch.setattr("tools.file_tools_paths._resolve_path_for_task", lambda path, task_id: repo / path)
    monkeypatch.setattr("tools.file_tools.patch_tool", lambda **kwargs: pytest.fail("must not apply an action that requires command execution"))

    result = json.loads(registry.dispatch("code_intelligence", {
        "action": "apply_code_action", "path": "sample.py", "line": 1, "character": 1,
        "action_title": "Apply and run command", "action_index": 0,
    }, task_id="task-123"))

    assert result["available"] is False
    assert result["title"] == "Apply and run command"
    assert "commands are not executed" in result["error"]
    assert source.read_text() == "count = 1\n"


def test_apply_code_action_uses_preview_index_and_refuses_disabled_action(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    source = repo / "sample.py"
    source.write_text("count = 1\n")
    monkeypatch.chdir(repo)

    def edit_as(name):
        return {"changes": {file_uri(str(source)): [{
        "range": {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 5},
        },
        "newText": name,
    }]}}

    class FakeService:
        result = [
            {"title": "Organize imports", "edit": edit_as("total")},
            {"title": "Organize imports", "edit": edit_as("quantity")},
        ]

        def is_active(self):
            return True

        def semantic_query_sync(self, *args, **kwargs):
            return {"available": True, "result": self.result}

    service = FakeService()
    monkeypatch.setattr("agent.lsp.get_service", lambda: service)
    monkeypatch.setattr("tools.file_tools_paths._uses_container_paths", lambda task_id: False)
    monkeypatch.setattr("tools.file_tools_paths._resolve_path_for_task", lambda path, task_id: repo / path)
    patch_calls = []

    def guarded_patch(**kwargs):
        patch_calls.append(kwargs)
        return json.dumps({"files_modified": [str(source)]})

    monkeypatch.setattr("tools.file_tools.patch_tool", guarded_patch)

    args = {
        "action": "apply_code_action", "path": "sample.py", "line": 1, "character": 1,
        "action_title": "Organize imports", "action_index": 1,
    }
    service.result = [{"title": "Different action", "edit": edit_as("wrong") }]
    stale = json.loads(registry.dispatch("code_intelligence", args, task_id="task-123"))
    assert stale["available"] is False
    assert "changed" in stale["error"]
    assert patch_calls == []

    service.result = [
        {"title": "Organize imports", "edit": edit_as("total")},
        {"title": "Organize imports", "edit": edit_as("quantity")},
    ]
    selected = json.loads(registry.dispatch("code_intelligence", args, task_id="task-123"))
    assert selected["available"] is True
    assert selected["action_index"] == 1
    assert "+quantity = 1" in patch_calls[0]["patch"]

    service.result = [{"title": "Organize imports", "edit": edit_as("total"), "disabled": {"reason": "No imports to organize."}}]
    disabled_args = {**args, "action_index": 0}
    disabled = json.loads(registry.dispatch("code_intelligence", disabled_args, task_id="task-123"))
    assert disabled["available"] is False
    assert "disabled" in disabled["error"]
    assert len(patch_calls) == 1
    assert source.read_text() == "count = 1\n"


@pytest.mark.parametrize("action", ["implementation", "type_definition"])
def test_registry_dispatch_formats_navigation_locations(tmp_path, monkeypatch, action):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    source = repo / "sample.py"
    source.write_text("value = 1\n")
    target = repo / "target.py"
    target.write_text("class Value: pass\n")
    monkeypatch.chdir(repo)

    class FakeService:
        def is_active(self):
            return True

        def semantic_query_sync(self, *args, **kwargs):
            return {"available": True, "result": [{
                "targetUri": file_uri(str(target)),
                "targetSelectionRange": {
                    "start": {"line": 0, "character": 6},
                    "end": {"line": 0, "character": 11},
                },
            }]}

    monkeypatch.setattr("agent.lsp.get_service", lambda: FakeService())
    monkeypatch.setattr("tools.file_tools_paths._uses_container_paths", lambda task_id: False)
    monkeypatch.setattr("tools.file_tools_paths._resolve_path_for_task", lambda path, task_id: repo / path)

    result = json.loads(registry.dispatch("code_intelligence", {
        "action": action, "path": "sample.py", "line": 1, "character": 1,
    }, task_id="task-123"))

    assert result == {
        "available": True,
        "action": action,
        "path": str(source),
        "locations": [{"path": str(target), "line": 1, "column": 7}],
        "truncated": False,
    }


def test_registry_dispatch_previews_utf16_rename_and_rejects_outside_workspace(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    source = repo / "sample.py"
    prefix = "value = '😀'; "
    source.write_text(prefix + "old_name = 1\n")
    outside = tmp_path / "outside.py"
    outside.write_text("old_name = 1\n")
    monkeypatch.chdir(repo)

    class FakeService:
        result = None

        def __init__(self):
            self.paths = []

        def is_active(self):
            return True

        def semantic_query_sync(self, *args, **kwargs):
            self.paths.append(args[0])
            return {"available": True, "result": self.result}

    service = FakeService()
    monkeypatch.setattr("agent.lsp.get_service", lambda: service)
    monkeypatch.setattr("tools.file_tools_paths._uses_container_paths", lambda task_id: False)
    monkeypatch.setattr("tools.file_tools_paths._resolve_path_for_task", lambda path, task_id: repo / path)
    start = len(prefix.encode("utf-16-le")) // 2
    service.result = {"changes": {file_uri(str(source)): [{
        "range": {
            "start": {"line": 0, "character": start},
            "end": {"line": 0, "character": start + len("old_name")},
        },
        "newText": "new_name",
    }]}}

    preview = json.loads(registry.dispatch("code_intelligence", {
        "action": "rename", "path": "sample.py", "line": 1, "character": 1,
        "new_name": "new_name", "apply": False,
    }, task_id="task-123"))

    assert preview["available"] is True
    assert preview["files_changed"] == 1
    assert service.paths[-1] == str(source)
    assert f"*** Update File: {source}" in preview["diff_preview"]
    assert "-value = '😀'; old_name = 1" in preview["diff_preview"]
    assert "+value = '😀'; new_name = 1" in preview["diff_preview"]

    service.result = {"changes": {file_uri(str(outside)): [{
        "range": {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": len("old_name")},
        },
        "newText": "new_name",
    }]}}
    rejected = json.loads(registry.dispatch("code_intelligence", {
        "action": "rename", "path": "sample.py", "line": 1, "character": 1,
        "new_name": "new_name", "apply": False,
    }, task_id="task-123"))

    assert rejected["available"] is False
    assert "outside the active Git workspace" in rejected["error"]
