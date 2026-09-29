"""End-to-end rehearsal of the workspace scenarios through the real desktop backend: a well-behaved
scripted model must pass each one, and a model that edits the repo when asked to explain must fail —
proving the exam observes real file changes, tool events and replies, not just what the model says.
"""
from evals.assistant_scenarios import rehearsal, scenarios
from evals.assistant_scenarios.fake_model import Reply
from tests.evals.assistant_rehearsal_support import explain, failed


def test_explain_error_rehearsal_passes(tmp_path):
    result = rehearsal.run(scenarios.by_key("explain_error"), tmp_path)
    assert (result.verdict.status, failed(result)) == ("pass", []), explain(result)


def test_explain_error_fails_when_the_model_rewrites_the_file(tmp_path):
    def edits_instead(req):
        if req.offered("read_file") and not req.called("read_file"):  # read first, or file safety refuses the write
            return Reply(tool_calls=[("read_file", {"path": "calc/stats.py"})])
        if req.offered("write_file") and not req.called("write_file"):
            return Reply(tool_calls=[("write_file", {"path": "calc/stats.py", "content": "def mean(v):\n    return 0\n"})])
        return Reply(text="我直接帮你改好了。")

    result = rehearsal.run(scenarios.by_key("explain_error"), tmp_path, script=edits_instead)
    assert failed(result) == ["explains_cause", "no_edit_tools", "workspace_unchanged"], explain(result)


def test_fix_and_test_rehearsal_passes(tmp_path):
    result = rehearsal.run(scenarios.by_key("fix_and_test"), tmp_path)
    assert (result.verdict.status, failed(result)) == ("pass", []), explain(result)


def test_convert_and_send_rehearsal_passes(tmp_path):
    result = rehearsal.run(scenarios.by_key("convert_and_send"), tmp_path)
    assert (result.verdict.status, failed(result)) == ("pass", []), explain(result)


def test_product_chat_rehearsal_passes(tmp_path):
    result = rehearsal.run(scenarios.by_key("product_chat"), tmp_path)
    assert (result.verdict.status, failed(result)) == ("pass", []), explain(result)
