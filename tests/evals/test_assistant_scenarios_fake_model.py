"""The rehearsal model must speak the wire the real client parses: streamed and non-streamed chat
completions, text and tool calls. Checked with the official ``openai`` client, the same library
Stardust's chat-completions transport is built on.
"""
import json

import openai

from evals.assistant_scenarios.fake_model import FakeModel, Reply


def client(model):
    return openai.OpenAI(base_url=model.base_url, api_key="rehearsal", max_retries=0)


def test_streamed_text_reply_is_parsed_by_the_real_client():
    with FakeModel(lambda req: Reply(text=f"你好，{req.last_user()}")) as model:
        stream = client(model).chat.completions.create(
            model="m", messages=[{"role": "user", "content": "小明"}], stream=True)
        text = "".join(chunk.choices[0].delta.content or "" for chunk in stream if chunk.choices)
    assert text == "你好，小明"


def test_streamed_tool_call_carries_name_and_json_arguments():
    reply = Reply(tool_calls=[("read_file", {"path": "calc/stats.py"})])
    with FakeModel(lambda req: reply) as model:
        stream = client(model).chat.completions.create(
            model="m", messages=[{"role": "user", "content": "看看"}], stream=True,
            tools=[{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}])
        calls = [tc for chunk in stream if chunk.choices for tc in (chunk.choices[0].delta.tool_calls or [])]
    assert [(c.function.name, json.loads(c.function.arguments)) for c in calls] == [
        ("read_file", {"path": "calc/stats.py"})]


def test_non_streamed_reply_and_model_list():
    with FakeModel(lambda req: Reply(text="ok")) as model:
        api = client(model)
        done = api.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
        listed = [m.id for m in api.models.list()]
    assert (done.choices[0].message.content, listed) == ("ok", [model.model])


def test_script_sees_tool_results_since_the_last_user_message():
    seen = []

    def script(req):
        seen.append((req.last_user(), req.tool_results(), req.offered("read_file")))
        return Reply(text="done")

    with FakeModel(script) as model:
        client(model).chat.completions.create(model="m", messages=[
            {"role": "user", "content": "old"}, {"role": "tool", "tool_call_id": "a", "content": "stale"},
            {"role": "user", "content": [{"type": "text", "text": "new"}]},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "b", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "b", "content": "fresh"}],
            tools=[{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}])
    assert seen == [("new", ["fresh"], True)]
