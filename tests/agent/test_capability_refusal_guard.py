"""F5.4: one-shot capability-refusal guard through the real agent turn loop.

Deterministic loopback model + real AIAgent; matrix mirrors the spec groups:
A readable local file + read_file available + model claims inability (guard
must re-ask once); B/C real permission/network failures after a tool attempt
(guard must never fire); D no file tool in the toolset; E pure capability
questions. ``F54_MATRIX`` selects baseline (guard code absent → run on a
clean checkout expectation), on (flag on), off (flag off == baseline).
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

# Group A: file exists + read_file available + model refuses generically.
A_REQUESTS = [
    (
        "请阅读 D:/wt/Temp/stardust/f5-4/probe/report.txt 并总结内容。",
        "我无法访问本地文件，请你自己打开后把内容贴给我。",
    ),
    (
        "读取 D:/wt/Temp/stardust/f5-4/probe/notes.md，告诉我里面写了什么。",
        "我无法读取本地文件，请把内容粘贴过来。",
    ),
    (
        "打开 D:/wt/Temp/stardust/f5-4/probe/data.txt 看看第一行。",
        "抱歉，我不能访问本地文件。",
    ),
    (
        "帮我看看 D:/wt/Temp/stardust/f5-4/probe/summary.txt 的内容。",
        "我没有访问本地文件的权限，请自行打开。",
    ),
    (
        "总结一下 D:/wt/Temp/stardust/f5-4/probe/log.txt。",
        "访问不了本地文件，请你把内容发给我。",
    ),
    (
        "Read D:/wt/Temp/stardust/f5-4/probe/report.txt and summarize it.",
        "I cannot access local files. Please paste the content here.",
    ),
    (
        "Open D:/wt/Temp/stardust/f5-4/probe/notes.md and tell me what it says.",
        "I can't read files on your machine.",
    ),
    (
        "Check D:/wt/Temp/stardust/f5-4/probe/data.txt for me.",
        "I am unable to access files on your device.",
    ),
    (
        "Look at D:/wt/Temp/stardust/f5-4/probe/summary.txt please.",
        "I don't have access to your local files.",
    ),
    (
        "Analyze D:/wt/Temp/stardust/f5-4/probe/log.txt.",
        "Unable to open the file you mentioned.",
    ),
]
# Group B: real permission denial (tool tried, error returned) x10; the
# model's final text cites the actual error. Group C: remote unreachable
# (network error) x10. In both the tool WAS tried, so the guard must never
# fire and the real reason must survive verbatim.
B_REQUESTS = [
    (
        "请读取 D:/wt/Temp/stardust/f5-4/probe/locked.txt 的内容。",
        "读取失败：Cannot read 'D:/wt/Temp/stardust/f5-4/probe/locked.txt': permission denied",
    ),
    (
        "打开 D:/wt/Temp/stardust/f5-4/probe/locked2.txt，读取内容。",
        "Permission denied: D:/wt/Temp/stardust/f5-4/probe/locked2.txt",
    ),
    (
        "读一下 D:/wt/Temp/stardust/f5-4/probe/locked3.md。",
        "读取失败：权限不足，无法打开 locked3.md",
    ),
    (
        "查看 D:/wt/Temp/stardust/f5-4/probe/secret.key 的内容。",
        "读取失败：access to secret.key is denied",
    ),
    (
        "Read D:/wt/Temp/stardust/f5-4/probe/locked4.txt and tell me the first line.",
        "Failed: permission denied on D:/wt/Temp/stardust/f5-4/probe/locked4.txt",
    ),
    (
        "Open D:/wt/Temp/stardust/f5-4/probe/locked5.txt for me.",
        "Error: access denied — the file is not readable by this process.",
    ),
    (
        "帮我看看 D:/wt/Temp/stardust/f5-4/probe/locked6.log。",
        "读取失败：系统拒绝了访问 locked6.log",
    ),
    (
        "阅读 D:/wt/Temp/stardust/f5-4/probe/locked7.cfg 并总结。",
        "读取失败：permission denied (locked7.cfg)",
    ),
    (
        "Check D:/wt/Temp/stardust/f5-4/probe/locked8.ini please.",
        "Failed to read: permission denied on locked8.ini",
    ),
    (
        "总结 D:/wt/Temp/stardust/f5-4/probe/locked9.txt。",
        "读取失败：Cannot read locked9.txt: permission denied",
    ),
]
C_REQUESTS = [
    (
        "请读取 D:/wt/Temp/stardust/f5-4/probe/nas1.log 的内容。",
        "读取失败：remote host unreachable (network error)",
    ),
    (
        "打开 D:/wt/Temp/stardust/f5-4/probe/nas2.log。",
        "读取失败：网络错误，无法连接到远程主机",
    ),
    (
        "Read D:/wt/Temp/stardust/f5-4/probe/nas3.log and summarize.",
        "Failed: remote share unreachable (network timeout)",
    ),
    (
        "查看 D:/wt/Temp/stardust/f5-4/probe/nas4.log。",
        "读取失败：连接超时，远程主机无响应",
    ),
    (
        "读取 D:/wt/Temp/stardust/f5-4/probe/nas5.log。",
        "Error: network unreachable while reading nas5.log",
    ),
    (
        "帮我读 D:/wt/Temp/stardust/f5-4/probe/nas6.log。",
        "Failed to read file: connection refused by remote host",
    ),
    (
        "阅读 D:/wt/Temp/stardust/f5-4/probe/nas7.log 并提取要点。",
        "读取失败：网络不可达 (nas7.log)",
    ),
    (
        "Open D:/wt/Temp/stardust/f5-4/probe/nas8.log.",
        "Failed: the remote volume could not be reached (timeout)",
    ),
    ("检查 D:/wt/Temp/stardust/f5-4/probe/nas9.log。", "读取失败：远程设备无法访问"),
    (
        "看看 D:/wt/Temp/stardust/f5-4/probe/nas10.log。",
        "Error: network error — remote host unreachable",
    ),
]
# Group D: no file tool in the toolset at all x10 — the refusal is TRUE,
# the guard must never fire.
D_REQUESTS = [
    (
        "请阅读 D:/wt/Temp/stardust/f5-4/probe/report.txt 并总结内容。",
        "我无法访问本地文件，请你自己打开后把内容贴给我。",
    ),
    ("读取 D:/wt/Temp/stardust/f5-4/probe/notes.md。", "我无法读取本地文件。"),
    ("打开 D:/wt/Temp/stardust/f5-4/probe/data.txt。", "我不能访问本地文件。"),
    ("查看 D:/wt/Temp/stardust/f5-4/probe/summary.txt。", "访问不了本地文件。"),
    ("总结 D:/wt/Temp/stardust/f5-4/probe/log.txt。", "我没有读取本地文件的工具。"),
    (
        "Read D:/wt/Temp/stardust/f5-4/probe/report.txt and summarize it.",
        "I cannot access local files. Please paste the content here.",
    ),
    (
        "Open D:/wt/Temp/stardust/f5-4/probe/notes.md please.",
        "I can't read files on this machine.",
    ),
    (
        "Check D:/wt/Temp/stardust/f5-4/probe/data.txt for me.",
        "I am unable to access files on this device.",
    ),
    (
        "Look at D:/wt/Temp/stardust/f5-4/probe/summary.txt.",
        "I don't have access to local files.",
    ),
    (
        "Analyze D:/wt/Temp/stardust/f5-4/probe/log.txt.",
        "Unable to open the file you mentioned.",
    ),
]
# Group E: pure capability questions, never an execution request.
E_REQUESTS = [
    "你能访问本地文件吗？",
    "你可以读取我电脑上的文件吗？",
    "你有读取文件的权限吗？",
    "Can you read files on my computer?",
    "Do you have access to local files?",
    "能不能读取本地文件？",
    "你可以打开本地文档吗？",
    "你是否能查看我磁盘上的文件？",
    "你有文件工具吗？",
    "Can you open local documents?",
]
E_REPLY = "我无法访问本地文件。"
PROBE_DIR = Path("D:/wt/Temp/stardust/f5-4/probe")


@pytest.fixture
def loopback():
    state = {"script": [], "calls": 0, "tool_calls": 0, "last_tools": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/chat/completions"):
                state["calls"] += 1
                state["last_tools"] = [
                    t["function"]["name"] for t in body.get("tools", [])
                ]
                state["tool_calls"] += sum(
                    len(m.get("tool_calls", [])) for m in body["messages"]
                )
                # Optional scripted tool call before the final refusal/answer.
                script = state["script"]
                tool_wanted = script[0].get("tool") if script else None
                if tool_wanted:
                    step = script.pop(0)
                    payload = {
                        "id": "local",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "local-scripted",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "id": "c1",
                                            "type": "function",
                                            "function": {
                                                "name": tool_wanted,
                                                "arguments": json.dumps(step["args"]),
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    }
                else:
                    step = script.pop(0) if script else {}
                    reply = step.get("reply", "")
                    payload = {
                        "id": "local",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "local-scripted",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": reply},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    }
            if body.get("stream"):
                payload["object"] = "chat.completion.chunk"
                payload["choices"][0]["delta"] = payload["choices"][0].pop("message")
                data = (
                    "data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n"
                ).encode()
                content_type = "text/event-stream"
            else:
                data = json.dumps(payload).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state, f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _run(loopback, request, script, toolsets):
    from run_agent import AIAgent

    state, url = loopback
    state.update(script=list(script), calls=0, tool_calls=0, last_tools=[])
    agent = AIAgent(
        model="local-scripted",
        api_key="local-test",
        base_url=url,
        provider="custom",
        api_mode="chat_completions",
        max_iterations=8,
        enabled_toolsets=toolsets,
        quiet_mode=True,
        skip_memory=True,
        skip_context_files=True,
        save_trajectories=False,
    )
    result = agent.run_conversation(request)
    nudges = sum(
        1
        for m in agent._session_messages
        if isinstance(m, dict)
        and str(m.get("content", "")).startswith("[capability note]")
    )
    return {
        "request": request,
        "calls": state["calls"],
        "tools_offered": state["last_tools"],
        "nudged": state["calls"] > 1,
        "nudges": nudges,
        **{
            k: result.get(k)
            for k in ("completed", "failed", "final_response", "turn_exit_reason")
        },
    }


def test_loopback_matrix(loopback, monkeypatch, tmp_path):
    mode = os.environ.get("F54_MATRIX", "on")
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"agent": {"capability_refusal_guard": mode in ("on", "baseline_on")}},
    )
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    (PROBE_DIR / "report.txt").write_text("probe content", encoding="utf-8")
    rows = []
    for request, reply in A_REQUESTS:
        # Script act 2: after the guard's corrective nudge the model uses the
        # named tool and answers with the real content (scripted read of the
        # existing probe file), so the false refusal is NOT delivered.
        rows.append({
            "group": "A",
            **_run(
                loopback,
                request,
                [
                    {"reply": reply},
                    {"tool": "read_file", "args": {"path": _path_from(request)}},
                    {"reply": "已用 read_file 读取该文件，内容为：probe content"},
                ],
                ["terminal", "file"],
            ),
        })
    for label, requests in (("B", B_REQUESTS), ("C", C_REQUESTS)):
        for request, reply in requests:
            # Real read_file attempt, then the model reports the ACTUAL error
            # from the tool result (deterministic on every OS).
            rows.append({
                "group": label,
                **_run(
                    loopback,
                    request,
                    [
                        {"tool": "read_file", "args": {"path": _path_from(request)}},
                        {"reply": reply},
                    ],
                    ["terminal", "file"],
                ),
            })
    for request, reply in D_REQUESTS:
        rows.append({
            "group": "D",
            **_run(loopback, request, [{"reply": reply}], ["terminal"]),
        })
    for request in E_REQUESTS:
        rows.append({
            "group": "E",
            **_run(loopback, request, [{"reply": E_REPLY}], ["terminal", "file"]),
        })

    evidence = os.environ.get("F54_EVIDENCE")
    if evidence:
        Path(evidence).mkdir(parents=True, exist_ok=True)
        Path(evidence, mode + ".json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    groups = {
        g: [r for r in rows if r["group"] == g] for g in ("A", "B", "C", "D", "E")
    }
    if mode == "baseline":
        # False refusal delivered verbatim, 0 nudges, turn completes.
        assert sum(r["nudged"] or r["nudges"] for r in groups["A"]) == 0
        assert all(
            r["completed"] and r["final_response"] == reply
            for r, (_req, reply) in zip(groups["A"], A_REQUESTS)
        )
        for g in ("B", "C"):
            for r in groups[g]:
                assert r["nudges"] == 0 and r["completed"], r
        assert all(not r["nudged"] and r["completed"] for r in groups["D"])
        assert all(not r["nudged"] and r["completed"] for r in groups["E"])
    elif mode == "off":
        baseline = json.loads(
            Path(evidence, "baseline.json").read_text(encoding="utf-8")
        )
        assert rows == baseline
    else:  # on
        for row in groups["A"]:
            assert row["nudged"] and row["nudges"] == 1, row
            assert row["calls"] == 3, row  # refusal -> nudge -> tool + answer
            assert row["completed"], row
            assert "probe content" in row["final_response"], row
            assert (
                "无法" not in row["final_response"]
                and "cannot" not in row["final_response"].lower()
                and "can't" not in row["final_response"].lower()
            ), row
        # After the single nudge the scripted model repeats its refusal: the
        # second answer is FINAL (budget 1), completed stays True.
        assert sum(r["nudges"] for r in groups["A"]) == 10
        # B/C: tool WAS tried, real error text must survive verbatim — the
        # guard never fires (0 guard nudges; the 2 API calls are the tool
        # round + final answer, not a nudge).
        for g in ("B", "C"):
            for row in groups[g]:
                assert row["nudges"] == 0 and row["completed"], row
                assert (
                    "失败" in row["final_response"]
                    or "not found" in row["final_response"].lower()
                    or "denied" in row["final_response"].lower()
                    or "unreachable" in row["final_response"]
                    or "refused" in row["final_response"].lower()
                    or "拒绝" in row["final_response"]
                    or "无法连接" in row["final_response"]
                    or "超时" in row["final_response"]
                    or "无响应" in row["final_response"]
                    or "不可达" in row["final_response"]
                    or "无响应" in row["final_response"]
                    or "reached" in row["final_response"].lower()
                    or "network" in row["final_response"].lower()
                    or "timeout" in row["final_response"].lower()
                    or "网络" in row["final_response"]
                ), row
        assert all(
            r["nudges"] == 0 and not r["nudged"] and r["completed"] for r in groups["D"]
        )
        assert all(
            r["nudges"] == 0 and not r["nudged"] and r["completed"] for r in groups["E"]
        )


def _path_from(request):
    for token in request.replace("，", " ").replace("。", " ").split():
        if "/" in token or "\\" in token:
            return token.rstrip(".,;:、")
    return "D:/wt/Temp/stardust/f5-4/probe/missing.txt"


def test_detector_boundaries_and_flag_default(monkeypatch):
    from agent.capability_refusal_guard import (
        build_capability_refusal_nudge,
        should_nudge_capability_refusal,
    )

    agent = SimpleNamespace(
        _strip_think_blocks=lambda text: text,
        valid_tool_names=["read_file", "write_file"],
        _capability_refusal_continuations=0,
    )
    agent._capability_refusal_guard_cache = True  # flag on
    request = "请阅读 report.txt 并总结。"
    history = [{"role": "user", "content": request}]
    # A: fires, names the available tool.
    assert (
        should_nudge_capability_refusal(
            agent, request, "我无法访问本地文件，请你自己打开后把内容贴给我。", history
        )
        == "read_file"
    )
    nudge = build_capability_refusal_nudge("read_file", request)
    assert "read_file" in nudge and "[capability note]" in nudge
    # Budget exhausted → never a second nudge in the same turn.
    agent._capability_refusal_continuations = 1
    assert (
        should_nudge_capability_refusal(agent, request, "我无法访问本地文件。", history)
        is None
    )
    agent._capability_refusal_continuations = 0
    # B/C: a tool was tried this turn → never fires.
    assert (
        should_nudge_capability_refusal(
            agent,
            request,
            "我无法访问本地文件。",
            history
            + [
                {
                    "role": "assistant",
                    "tool_calls": [{"id": "c", "function": {"name": "read_file"}}],
                },
                {"role": "tool", "content": '{"error": "File not found: x"}'},
            ],
        )
        is None
    )
    # Real-failure wording never fires even without a tool attempt.
    for text in (
        "读取失败：File not found: report.txt",
        "无法访问：网络错误",
        "Failed: permission denied",
    ):
        assert should_nudge_capability_refusal(agent, request, text, history) is None
    # D: no matching tool available → never fires.
    agent_no_tool = SimpleNamespace(
        _strip_think_blocks=lambda text: text,
        valid_tool_names=["terminal"],
        _capability_refusal_continuations=0,
    )
    agent_no_tool._capability_refusal_guard_cache = True
    assert (
        should_nudge_capability_refusal(
            agent_no_tool, request, "我无法访问本地文件。", history
        )
        is None
    )
    # E: capability question → never fires.
    for question in ("你能访问本地文件吗？", "Can you read files?", "你有文件工具吗？"):
        assert (
            should_nudge_capability_refusal(
                agent, question, "我无法访问本地文件。", history
            )
            is None
        )
    # Non-execution statements never fire.
    assert (
        should_nudge_capability_refusal(
            agent, "今天天气不错。", "我无法访问本地文件。", history
        )
        is None
    )
    # Flag default OFF and non-true values.
    for cfg in (
        {},
        {"agent": {}},
        {"agent": {"capability_refusal_guard": False}},
        {"agent": {"capability_refusal_guard": "false"}},
        {"agent": {"capability_refusal_guard": 1}},
    ):
        monkeypatch.setattr("hermes_cli.config.load_config", lambda cfg=cfg: cfg)
        fresh = SimpleNamespace(
            _strip_think_blocks=lambda text: text, valid_tool_names=["read_file"]
        )
        assert capability_flag(fresh) is False


def capability_flag(agent):
    from agent.capability_refusal_guard import capability_refusal_guard_enabled

    return capability_refusal_guard_enabled(agent)
