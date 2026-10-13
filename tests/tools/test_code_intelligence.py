import json
import subprocess

import tools.code_intelligence  # noqa: F401 - registers the tool for real registry dispatch
from agent.lsp.client import file_uri
from tools.registry import registry


def test_registry_dispatch_previews_utf16_rename_and_rejects_outside_workspace(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    source = repo / "sample.py"
    prefix = "value = '😀'; "
    source.write_text(prefix + "old_name = 1\n")
    outside = tmp_path / "outside.py"
    outside.write_text("old_name = 1\n")
    monkeypatch.chdir(tmp_path)

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
