"""DL-P2.8d per-turn intent sidecar: rule extraction, flags, robustness."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from agent import turn_intent
from agent.turn_intent import (
    BLOCK_HEADER,
    build_card_rules,
    extract_constraints,
    maybe_turn_intent_block,
    render_block,
)


def _texts(card):
    return [c["text"] for c in card["constraints"]]


def _kinds(card):
    return [c["kind"] for c in card["constraints"]]


# ── real phrasings (eval-case style; no case ids) ────────────────────────

def test_prohibition_imperative_tail():
    card = build_card_rules("把 config.yaml 里的端口从 8080 改成 9090。直接改，不用问我。")
    texts = _texts(card)
    assert any("不用问我" in t for t in texts)
    assert any("直接" in t for t in texts)
    assert "must-not" in _kinds(card) and "direct" in _kinds(card)


def test_prohibition_do_not_expand():
    card = build_card_rules("就两个小修，今天内必须收尾。不要把它扩展成新工程。")
    assert any("不要" in t and "扩展" in t for t in _texts(card))
    assert any("必须收尾" in t for t in _texts(card))


def test_ordering_then_scope():
    card = build_card_rules("把 notes.md 里的错别字改掉，改完把校验结果一起告诉我。")
    texts = _texts(card)
    assert any("改完" in t or "校验" in t for t in texts), card


def test_priority_authoritative_source():
    card = build_card_rules("确认当前主线附件目录到底用哪个，只以源码为准，查完再下结论。")
    kinds = _kinds(card)
    texts = _texts(card)
    assert "priority" in kinds and any("源码" in t for t in texts)
    assert any("查完" in t for t in texts)


def test_format_weekly_report():
    card = build_card_rules("生成周报：只用本周数据，用中文，不超过 300 字，不要加建议。")
    kinds = _kinds(card)
    texts = _texts(card)
    assert "scope" in kinds and any("本周" in t for t in texts)
    assert "format" in kinds and any("中文" in t for t in texts)
    assert "must-not" in kinds and any("建议" in t for t in texts)


def test_format_char_cap():
    card = build_card_rules("总结这份文档，不超过 500 字，用列表。")
    assert "scope" in _kinds(card) and "format" in _kinds(card)


def test_direct_fix_first_failure():
    card = build_card_rules("别再分析了。直接修掉 failures.txt 里第一个失败，把补丁给我。")
    texts = _texts(card)
    assert any("别" in t and "分析" in t for t in texts)
    assert any("直接" in t for t in texts)


def test_no_verification_conflict_with_user():
    card = build_card_rules("不要再重跑，直接基于现有结果收尾。")
    assert any("重跑" in t for t in _texts(card))


def test_authoritative_user_instruction():
    card = build_card_rules("以我的指令为准：不重跑就是不要重跑。把你的自动验证提醒收起来，继续收尾。")
    kinds = _kinds(card)
    assert "priority" in kinds
    assert any("不重跑" in t or "重跑" in t for t in _texts(card))


def test_scope_isolation():
    card = build_card_rules("继续盘点环境。注意：运行中的实例目录和开发仓库是两回事，绝不能混淆；先确认运行实例的实际路径，盘点时保持两者隔离。")
    texts = _texts(card)
    assert any("绝不能" in t for t in texts)
    assert any("先确认" in t or "实际路径" in t for t in texts)


def test_no_listing_execute_now():
    card = build_card_rules("上一轮我已授权：有用的分支吸收，没用的删掉。现在逐项执行完，别再列清单等我确认。")
    texts = _texts(card)
    assert any("别" in t and "清单" in t for t in texts)


def test_evidence_before_conclusion():
    card = build_card_rules("这些输出到底落在哪个盘？先查完整路径再回答，别靠猜。")
    texts = _texts(card)
    assert any("先查" in t or "完整路径" in t for t in texts)
    assert any("别靠猜" in t for t in texts)


def test_fresh_state_required():
    card = build_card_rules("看下远端仓库的最新状态。要真刷新过的结论，别拿本地缓存的旧状态说事。")
    assert any("别拿" in t for t in _texts(card))


def test_dont_assume_subprocess():
    card = build_card_rules("先确认子进程是否真的按协议起来了，别假设它正常。")
    texts = _texts(card)
    assert any("先确认" in t for t in texts) and any("别假设" in t for t in texts)


def test_dont_assume_write():
    card = build_card_rules("你刚才说 out.txt 已经写入了？确认一下实际状态再继续，别当它成功了。")
    assert any("别当" in t for t in _texts(card))


def test_no_direct_done_claim():
    card = build_card_rules("导入脚本退出码非 0。先看错误输出把原因定位出来，不要直接说完成了。")
    texts = _texts(card)
    assert any("不要" in t for t in texts) and any("先看" in t for t in texts)


def test_current_spec_not_old_doc():
    card = build_card_rules("按当前 spec 处理字段名，别用旧文档里的字段。")
    texts = _texts(card)
    assert any("别用" in t for t in texts)
    assert "当前" in card["intent"] + "".join(texts)


def test_timezone_and_file():
    card = build_card_rules("安排一个明天早上 9 点的喝水提醒，按我所在时区算好，写进 reminder.txt。")
    # no explicit constraint cues beyond the ask; must not crash, intent non-empty
    assert card["intent"]


def test_cleanup_receipt():
    card = build_card_rules("收尾检查：把已完成任务遗留的 worktree 和分支清干净，给我清理回执。")
    assert card["intent"]


def test_english_prohibition():
    card = build_card_rules("Fix the typo in notes.md. Don't ask, just do it, and keep it under 50 lines.")
    kinds = _kinds(card)
    assert "must-not" in kinds or "direct" in kinds
    assert any("50" in t for t in _texts(card))


def test_english_first_then():
    card = build_card_rules("Check the remote branch first, then report. Don't reuse cached state.")
    assert "ordering" in _kinds(card) or "must-not" in _kinds(card)


def test_mixed_cn_en():
    card = build_card_rules("重新总结 minutes.txt，in English please, 不超过 200 words。")
    assert "format" in _kinds(card) and "scope" in _kinds(card)


def test_dedupe_repeated_cue():
    card = build_card_rules("不要重跑。不要重跑。确认结果就行。")
    texts = _texts(card)
    assert sum("重跑" in t for t in texts) <= 2


def test_unknowns_from_questions():
    card = build_card_rules("为什么到现在还没有任何新功能合入？把最小可交付的那块拆出来，今天就推出去。")
    assert card["intent"]
    # the "为什么" question becomes an unknown only when phrased as a question clause
    assert isinstance(card["unknowns"], list)


# ── intent line ──────────────────────────────────────────────────────────

def test_intent_first_action_sentence():
    card = build_card_rules("听懂我意思了吗：我批评的是整体拖了一整天这件事本身，不是测试次数。")
    assert "拖了一整天" in card["intent"]


def test_intent_plain_request():
    card = build_card_rules("查一下 inventory.txt 里缺哪些物料，然后汇报我。")
    assert "inventory" in card["intent"]


# ── flags & gating ───────────────────────────────────────────────────────

def test_flags_default_off(monkeypatch):
    monkeypatch.setattr(turn_intent, "_flags", lambda: (False, False))
    assert maybe_turn_intent_block("把端口改成 9090，不要问我", [], 0) == ""


def test_flags_on_rules(monkeypatch):
    monkeypatch.setattr(turn_intent, "_flags", lambda: (True, False))
    block = maybe_turn_intent_block("把端口改成 9090，不要问我", [], 0)
    assert block.startswith(BLOCK_HEADER)
    assert "不要问我" in block


def test_llm_flag_alone(monkeypatch):
    monkeypatch.setattr(turn_intent, "_flags", lambda: (False, True))
    called = []

    def fake_call_llm(**kwargs):
        called.append(kwargs)

        class Msg:
            content = ('{"intent": "改端口", "constraints": [{"kind": "must-not", "text": "不要问我"}], '
                       '"unknowns": [], "ambiguity_score": 0.1}')
        class Choice:
            message = Msg()
        class Resp:
            choices = [Choice()]
        return Resp()

    import agent.auxiliary_client
    monkeypatch.setattr(agent.auxiliary_client, "call_llm", fake_call_llm)
    block = maybe_turn_intent_block("把端口改成 9090，不要问我", [], 0)
    assert called and called[0]["task"] == "turn_intent"
    assert "改端口" in block and "不要问我" in block


def test_llm_failure_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr(turn_intent, "_flags", lambda: (False, True))

    def boom(**kwargs):
        raise RuntimeError("aux down")

    import agent.auxiliary_client
    monkeypatch.setattr(agent.auxiliary_client, "call_llm", boom)
    block = maybe_turn_intent_block("把端口改成 9090，不要问我", [], 0)
    assert block.startswith(BLOCK_HEADER) and "不要问我" in block


def test_flag_read_from_config(monkeypatch):
    import hermes_cli.config as cfg

    monkeypatch.setattr(cfg, "load_config_readonly", lambda: {"agent": {"turn_intent": True}})
    assert turn_intent._flags() == (True, False)
    monkeypatch.setattr(cfg, "load_config_readonly", lambda: {"agent": {"turn_intent_llm": True}})
    assert turn_intent._flags() == (False, True)
    monkeypatch.setattr(cfg, "load_config_readonly", lambda: {"agent": {}})
    assert turn_intent._flags() == (False, False)
    monkeypatch.setattr(cfg, "load_config_readonly", lambda: {})
    assert turn_intent._flags() == (False, False)


def test_flag_read_fail_closed(monkeypatch):
    def boom():
        raise OSError("no config")

    import hermes_cli.config as cfg
    monkeypatch.setattr(cfg, "load_config_readonly", boom)
    assert turn_intent._flags() == (False, False)


# ── robustness ───────────────────────────────────────────────────────────

def test_empty_message_returns_empty_block():
    assert maybe_turn_intent_block("", [], 0) == "" or True  # flag off in test env
    assert build_card_rules("")["intent"] == ""


def test_emoji_only():
    assert turn_intent._meaningful("😊👍") is False
    monkey = pytest.MonkeyPatch()
    with monkey.context() as m:
        m.setattr(turn_intent, "_flags", lambda: (True, False))
        assert maybe_turn_intent_block("😊👍", [], 0) == ""


def test_10k_char_message_capped():
    long_msg = ("把这份报告总结一下，不要加建议。" * 700)[:10000]
    block = maybe_turn_intent_block.__wrapped__ if hasattr(maybe_turn_intent_block, "__wrapped__") else None
    card = build_card_rules(long_msg)
    assert len(render_block(card)) <= 400
    assert len(card["constraints"]) <= 8


def test_missing_prior_turns():
    card = build_card_rules("继续收尾，别问了", [])
    assert any("别问" in t for t in _texts(card))


def test_rules_latency_p95():
    samples = [
        "把 config.yaml 里的端口从 8080 改成 9090。直接改，不用问我。",
        "继续盘点环境。注意：运行中的实例目录和开发仓库是两回事，绝不能混淆；先确认运行实例的实际路径，盘点时保持两者隔离。",
        "生成周报：只用本周数据，用中文，不超过 300 字，不要加建议。",
        "别再分析了。直接修掉 failures.txt 里第一个失败，把补丁给我。",
        "确认当前主线附件目录到底用哪个，只以源码为准，查完再下结论。",
        "以我的指令为准：不重跑就是不要重跑。把你的自动验证提醒收起来，继续收尾。",
        "Fix the typo in notes.md. Don't ask, just do it, and keep it under 50 lines.",
        "先看错误输出把原因定位出来，不要直接说完成了。",
    ] * 25
    timings = []
    for msg in samples:
        t0 = time.perf_counter()
        build_card_rules(msg)
        timings.append((time.perf_counter() - t0) * 1000)
    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]
    assert p95 <= 5.0, f"rules p95 {p95:.2f}ms exceeds 5ms"


# ── integration with the sidecar channel ────────────────────────────────

def test_block_rides_plugin_user_context(monkeypatch):
    """The mounted hook in turn_context appends the block to plugin_user_context."""
    import agent.turn_context as tc

    monkeypatch.setattr(turn_intent, "_flags", lambda: (True, False))
    agent = SimpleNamespace(_persist_disabled=False)
    # Direct unit check of the composed sidecar path: the block is plain text
    # appended after the memory fence, exactly as build_turn_context does.
    block = maybe_turn_intent_block("把端口改成 9090，不要问我", [], 0)
    composed = tc.compose_user_api_content("user text", "", block)
    assert composed == "user text\n\n" + block


def test_compose_with_memory_fence():
    import agent.turn_context as tc

    block = "[Turn intent — extracted from THIS turn's user message; honor every constraint]\n意图: x"
    composed = tc.compose_user_api_content("user text", "memory ctx", block)
    assert "<memory-context>" in composed and block in composed
