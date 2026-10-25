"""Real-scenario replay (DL-P2.9): 6 multi-session conversations through the
ACTUAL ``_run_review_in_thread`` background path in a temp HOME, then diff the
fact store.

The extraction model call is intercepted at the auxiliary-client seam (no real
API, no cost); everything else — the background_review thread mount, fact
pipeline decisions, bi-temporal store writes, recall — is production code.

Usage (from the repo root, repo venv python):
  python -m evals.digital_life.fact_replay --home HOME --output OUT.json
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from evals.digital_life.memory_retrieval import HOME_VARS, assert_home

# Multi-session conversation script: later sessions change earlier facts.
CONVERSATIONS = [
    {
        "session": "s1-moving",
        "model_reply": json.dumps({"facts": [
            {"subject": "residence", "predicate": "居住地", "content": "阿豪住在吉隆坡。", "confidence": 0.9},
            {"subject": "job", "predicate": "职业", "content": "阿豪在银行做分析师。", "confidence": 0.9},
        ]}, ensure_ascii=False),
        "messages": [
            {"role": "user", "content": "我刚搬到吉隆坡，在银行找了份分析师的工作。"},
            {"role": "assistant", "content": "好的，记下了：吉隆坡 + 银行分析师。"},
        ],
    },
    {
        "session": "s2-relocate",
        "model_reply": json.dumps({"facts": [
            {"subject": "residence", "predicate": "居住地", "content": "阿豪住在米兰。", "confidence": 0.95},
        ]}, ensure_ascii=False),
        "messages": [
            {"role": "user", "content": "我搬去米兰了，吉隆坡的房子退了。"},
            {"role": "assistant", "content": "了解，居住地更新为米兰。"},
        ],
    },
    {
        "session": "s3-job-change",
        "model_reply": json.dumps({"facts": [
            {"subject": "job", "predicate": "职业", "content": "阿豪是独立软件开发者。", "confidence": 0.9},
            {"subject": "editor", "predicate": "编辑器", "content": "阿豪用 VS Code 写代码。", "confidence": 0.85},
        ]}, ensure_ascii=False),
        "messages": [
            {"role": "user", "content": "我从银行离职了，现在做独立开发，用 VS Code。"},
            {"role": "assistant", "content": "好的，职业更新为独立开发者，编辑器 VS Code。"},
        ],
    },
    {
        "session": "s4-editor-swap",
        "model_reply": json.dumps({"facts": [
            {"subject": "editor", "predicate": "编辑器", "content": "阿豪用 Cursor 写代码。", "confidence": 0.9},
        ]}, ensure_ascii=False),
        "messages": [
            {"role": "user", "content": "最近换 Cursor 了，VS Code 不用了。"},
            {"role": "assistant", "content": "好的，编辑器更新为 Cursor。"},
        ],
    },
    {
        "session": "s5-retract-and-repeat",
        "model_reply": json.dumps({"facts": [
            {"subject": "gym-membership", "predicate": "健身房会员", "content": "阿豪退了健身房会员。", "confidence": 0.9,
             "retracted": True},
            {"subject": "residence", "predicate": "居住地", "content": "阿豪住在米兰。", "confidence": 0.95},
        ]}, ensure_ascii=False),
        "messages": [
            {"role": "user", "content": "健身房会员退了。对了再确认下我还住在米兰。"},
            {"role": "assistant", "content": "好，健身房会员已关闭记录；米兰不变。"},
        ],
    },
    {
        "session": "s6-model-offline",
        "model_reply": "not-json-garbage",
        "messages": [
            {"role": "user", "content": "随便聊聊今天天气不错。"},
            {"role": "assistant", "content": "是啊，不错。"},
        ],
    },
]


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)

    home = assert_home(args.home)
    for key in HOME_VARS:
        os.environ[key] = str(home)
    cfg = home / 'config.yaml'
    cfg.write_text('memory:\n  fact_pipeline: true\n', encoding='utf-8')

    from tools import fact_store as fs

    def store_snapshot():
        return [
            {"id": r["id"], "subject": r["subject"], "content": r["content"],
             "valid_to": r.get("valid_to"), "superseded_by": r.get("superseded_by")}
            for r in fs.load_facts(include_history=True)
        ]

    before = store_snapshot()

    import agent.background_review as background_review

    class _FakeAgent:
        """Only what _run_review_in_thread touches around the fact hook."""

        session_id = "replay"
        provider = 'custom'

        def _emit_auxiliary_failure(self, *a, **k):
            pass

    _orig_emit = background_review._parent_can_emit_tool_calls
    _orig_resolve = background_review._resolve_review_runtime
    timeline = []
    for conv in CONVERSATIONS:
        agent = _FakeAgent()
        agent.session_id = conv["session"]
        reply_holder = {"text": conv["model_reply"]}

        # Intercept at the pipeline module itself: the hook does a fresh
        # ``from tools.fact_pipeline import run_fact_extraction`` inside the
        # thread body, so patching the module attribute is what reaches it.
        import tools.fact_pipeline as fp

        _orig_extraction = fp.run_fact_extraction

        def fake_extraction(messages, **kw):
            reply = reply_holder["text"]
            if reply == "not-json-garbage":
                return {"skipped": "malformed", "error": "simulated garbage"}
            candidates = json.loads(reply)["facts"]
            ops = fp.decide_fact_ops(candidates, source_session_id=kw.get("source_session_id", ""))
            results = fp.apply_fact_ops(ops, source="fact-pipeline",
                                        source_session_id=kw.get("source_session_id", ""))
            counts = {}
            for r in results:
                counts[r.get("action", "?")] = counts.get(r.get("action", "?"), 0) + 1
            return {"counts": counts, "results": results}

        fp.run_fact_extraction = fake_extraction
        started = time.perf_counter()
        # Make the thread bail right after the fact hook (provider cannot emit
        # tool calls), so no fork runs — exactly the seam this replay tests.
        background_review._parent_can_emit_tool_calls = lambda a: False
        background_review._resolve_review_runtime = lambda a, t=None: {"routed": False}
        try:
            background_review._run_review_in_thread(agent, list(conv["messages"]), "prompt")
            elapsed = time.perf_counter() - started
            result = {"ok": True}
        finally:
            fp.run_fact_extraction = _orig_extraction
            background_review._parent_can_emit_tool_calls = _orig_emit
            background_review._resolve_review_runtime = _orig_resolve
        timeline.append({"session": conv["session"], "result": result,
                         "elapsed_s": round(elapsed, 3)})

    after = store_snapshot()

    before_ids = {b["id"] for b in before}
    added = [r for r in after if r["id"] not in before_ids]
    closed = [r for r in after if r.get("valid_to") and r["id"] not in
              {b["id"] for b in before if b.get("valid_to")}]

    report = {
        "meta": {"conversations": len(CONVERSATIONS), "home": str(home),
                 "path": "real _run_review_in_thread"},
        "before_rows": len(before),
        "after_rows": len(after),
        "added": added,
        "closed": closed,
        "timeline": timeline,
        "store_after": after,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({"conversations": len(CONVERSATIONS), "before": len(before),
                      "after": len(after), "added": len(added), "closed": len(closed)},
                     ensure_ascii=False))


_orig_emit = None
_orig_resolve = None

if __name__ == '__main__':
    main()
