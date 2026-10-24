"""Pure tool-call guardrail primitive tests."""

import json

from agent.tool_guardrails import (
    ToolCallGuardrailConfig,
    ToolCallGuardrailController,
    ToolCallSignature,
    canonical_tool_args,
    classify_tool_failure,
)


def test_tool_call_signature_hashes_canonical_nested_unicode_args_without_exposing_raw_args():
    args_a = {
        "z": [{"β": "☤", "a": 1}],
        "a": {"y": 2, "x": "secret-token-value"},
    }
    args_b = {
        "a": {"x": "secret-token-value", "y": 2},
        "z": [{"a": 1, "β": "☤"}],
    }

    assert canonical_tool_args(args_a) == canonical_tool_args(args_b)
    sig_a = ToolCallSignature.from_call("web_search", args_a)
    sig_b = ToolCallSignature.from_call("web_search", args_b)

    assert sig_a == sig_b
    assert len(sig_a.args_hash) == 64
    metadata = sig_a.to_metadata()
    assert metadata == {"tool_name": "web_search", "args_hash": sig_a.args_hash}
    assert "secret-token-value" not in json.dumps(metadata)
    assert "☤" not in json.dumps(metadata)


def test_default_config_is_soft_warning_only_with_hard_stop_disabled():
    cfg = ToolCallGuardrailConfig()

    assert cfg.warnings_enabled is True
    assert cfg.hard_stop_enabled is False
    assert cfg.non_interactive_hard_stop_enabled is True
    assert cfg.exact_failure_warn_after == 2
    assert cfg.same_tool_failure_warn_after == 3
    assert cfg.no_progress_warn_after == 2
    assert cfg.exact_failure_block_after == 5
    assert cfg.same_tool_failure_halt_after == 8
    assert cfg.no_progress_block_after == 5


def test_config_parses_nested_warn_and_hard_stop_thresholds():
    cfg = ToolCallGuardrailConfig.from_mapping(
        {
            "warnings_enabled": False,
            "hard_stop_enabled": True,
            "warn_after": {
                "exact_failure": 3,
                "same_tool_failure": 4,
                "idempotent_no_progress": 5,
            },
            "hard_stop_after": {
                "exact_failure": 6,
                "same_tool_failure": 7,
                "idempotent_no_progress": 8,
            },
        }
    )

    assert cfg.warnings_enabled is False
    assert cfg.hard_stop_enabled is True
    assert cfg.exact_failure_warn_after == 3
    assert cfg.same_tool_failure_warn_after == 4
    assert cfg.no_progress_warn_after == 5
    assert cfg.exact_failure_block_after == 6
    assert cfg.same_tool_failure_halt_after == 7
    assert cfg.no_progress_block_after == 8


def test_gateway_platform_defaults_to_hard_stop_without_changing_interactive_defaults():
    interactive_configs = [
        ToolCallGuardrailConfig.from_mapping({}, platform=platform)
        for platform in ("cli", "tui", "desktop", "acp")
    ]
    telegram_cfg = ToolCallGuardrailConfig.from_mapping({}, platform="telegram")
    cron_cfg = ToolCallGuardrailConfig.from_mapping({}, platform="cron")

    assert all(cfg.hard_stop_enabled is False for cfg in interactive_configs)
    assert telegram_cfg.hard_stop_enabled is True
    assert cron_cfg.hard_stop_enabled is True


def test_non_interactive_hard_stop_can_be_disabled_explicitly():
    cfg = ToolCallGuardrailConfig.from_mapping(
        {"non_interactive_hard_stop_enabled": False},
        platform="telegram",
    )

    assert cfg.hard_stop_enabled is False
    assert cfg.non_interactive_hard_stop_enabled is False


def test_default_repeated_identical_failed_call_warns_without_blocking():
    controller = ToolCallGuardrailController()
    args = {"query": "same"}

    decisions = []
    for _ in range(5):
        assert controller.before_call("web_search", args).action == "allow"
        decisions.append(
            controller.after_call("web_search", args, '{"error":"boom"}', failed=True)
        )

    assert decisions[0].action == "allow"
    assert [d.action for d in decisions[1:]] == ["warn", "warn", "warn", "warn"]
    assert {d.code for d in decisions[1:]} == {"repeated_exact_failure_warning"}
    assert controller.before_call("web_search", args).action == "allow"
    assert controller.halt_decision is None


def test_hard_stop_enabled_blocks_repeated_exact_failure_before_next_execution():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            exact_failure_warn_after=2,
            exact_failure_block_after=2,
            same_tool_failure_halt_after=99,
        )
    )
    args = {"query": "same"}

    assert controller.before_call("web_search", args).action == "allow"
    first = controller.after_call("web_search", args, '{"error":"boom"}', failed=True)
    assert first.action == "allow"

    assert controller.before_call("web_search", args).action == "allow"
    second = controller.after_call("web_search", args, '{"error":"boom"}', failed=True)
    assert second.action == "warn"
    assert second.code == "repeated_exact_failure_warning"

    blocked = controller.before_call("web_search", args)
    assert blocked.action == "block"
    assert blocked.code == "repeated_exact_failure_block"
    assert blocked.count == 2














def test_skill_read_tools_are_idempotent_and_block_repeated_identical_success_output():
    cases = [
        (
            "skill_view",
            {"name": "gui-agent-ml-operations"},
            '{"success":true,"name":"gui-agent-ml-operations","content":"same"}',
        ),
        (
            "skills_list",
            {"category": "mlops"},
            '{"success":true,"skills":[{"name":"gui-agent-ml-operations"}]}',
        ),
    ]

    for tool_name, args, result in cases:
        controller = ToolCallGuardrailController(
            ToolCallGuardrailConfig(
                hard_stop_enabled=True,
                no_progress_warn_after=2,
                no_progress_block_after=2,
            )
        )

        assert controller.before_call(tool_name, args).action == "allow"
        assert controller.after_call(tool_name, args, result, failed=False).action == "allow"
        assert controller.before_call(tool_name, args).action == "allow"
        warn = controller.after_call(tool_name, args, result, failed=False)
        assert warn.action == "warn"
        assert warn.code == "idempotent_no_progress_warning"

        blocked = controller.before_call(tool_name, args)
        assert blocked.action == "block"
        assert blocked.code == "idempotent_no_progress_block"


def test_mutating_or_unknown_tools_are_not_blocked_for_repeated_identical_success_output_by_default():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(no_progress_warn_after=2, no_progress_block_after=2)
    )

    for _ in range(3):
        assert controller.before_call("write_file", {"path": "/tmp/x", "content": "x"}).action == "allow"
        assert controller.after_call("write_file", {"path": "/tmp/x", "content": "x"}, "ok", failed=False).action == "allow"
        assert controller.before_call("custom_tool", {"x": 1}).action == "allow"
        assert controller.after_call("custom_tool", {"x": 1}, "ok", failed=False).action == "allow"


def test_identical_call_streak_halts_any_tool_when_hard_stop_enabled():
    # #89069 / #100849 bundle: a model replaying the same SUCCESSFUL
    # terminal/skill_view call with a byte-identical result is not covered by
    # the idempotent_tools no-progress block. The consecutive-identical
    # streak (observe_call) is tool-agnostic; under hard_stop it must halt.
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(hard_stop_enabled=True, no_progress_block_after=5)
    )
    args = {"command": "stardust config get memory.provider"}
    for i in range(1, 5):
        controller.after_call("terminal", args, "local\n", failed=False)
        controller.observe_call("terminal", args, "local\n", failed=False)
        assert controller.halt_decision is None, f"halted early at {i}"

    controller.after_call("terminal", args, "local\n", failed=False)
    controller.observe_call("terminal", args, "local\n", failed=False)
    halt = controller.halt_decision
    assert halt is not None and halt.should_halt
    assert halt.code == "identical_call_streak_halt"
    assert halt.tool_name == "terminal" and halt.count == 5


def test_identical_call_streak_never_halts_when_hard_stop_disabled_or_for_pollers():
    soft = ToolCallGuardrailController(
        ToolCallGuardrailConfig(hard_stop_enabled=False, no_progress_block_after=2)
    )
    for _ in range(6):
        soft.observe_call("terminal", {"command": "ls"}, "a\nb\n", failed=False)
    assert soft.halt_decision is None  # notice-only in interactive sessions

    hard = ToolCallGuardrailController(
        ToolCallGuardrailConfig(hard_stop_enabled=True, no_progress_block_after=2)
    )
    for _ in range(6):
        hard.observe_call("process_manage", {"action": "poll", "session_id": "p1"}, "running", failed=False)
    assert hard.halt_decision is None  # an unchanged poll is legitimate progress

    # A changed result resets the streak.
    for i in range(6):
        hard.observe_call("terminal", {"command": "date"}, f"t{i}", failed=False)
    assert hard.halt_decision is None






# ── Per-turn runaway-loop caps (Claude Code v2.1.212, Week 29) ──────────────

from agent.tool_guardrails import LoopCapConfig  # noqa: E402






def test_loop_cap_zero_disables_and_junk_falls_back():
    # 0 is a legitimate "unlimited" value; negatives / junk fall back to default.
    assert LoopCapConfig.from_mapping({"max_web_searches": 0}).max_web_searches == 0
    assert LoopCapConfig.from_mapping({"max_web_searches": -5}).max_web_searches == 50
    assert LoopCapConfig.from_mapping({"max_subagents": "nope"}).max_subagents == 50


def test_web_search_cap_blocks_after_limit_regardless_of_hard_stop():
    # Loop caps fire even with hard_stop_enabled=False (the per-turn loop
    # detector's flag). Each distinct query avoids the loop detector so we know
    # the block came from the loop cap, not exact-failure repetition.
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=False,
            loop_caps=LoopCapConfig(max_web_searches=3),
        )
    )
    for i in range(3):
        assert controller.before_call("web_search", {"query": f"q{i}"}).action == "allow"
    decision = controller.before_call("web_search", {"query": "q4"})
    assert decision.action == "block"
    assert decision.code == "loop_web_search_cap"
    assert decision.should_halt is True












# ── Legitimate flows must survive hard stops (Teknium, Sep 2026) ────────────
# Hard stops default ON for unattended platforms. These pin the flows that
# must NEVER be cut off there: edit -> re-run loops, diagnostic sweeps of
# distinct red commands, and browser retry-after-action — while the pure
# replay (same call, nothing changed between attempts) is still stopped.

_HARD = lambda: ToolCallGuardrailController(  # noqa: E731
    ToolCallGuardrailConfig(hard_stop_enabled=True)
)
_PYTEST = {"command": "pytest tests/test_x.py -q"}
_RED = '{"output": "1 failed", "exit_code": 1}'


def _run_red(c, args=_PYTEST):
    assert c.before_call("terminal", args).allows_execution
    return c.after_call("terminal", args, _RED, failed=True)


def test_fix_retest_loop_is_never_hard_stopped():
    c = _HARD()
    for i in range(12):
        d = _run_red(c)
        assert not d.should_halt, f"halted on red run {i + 1}"
        # the model edits between runs — a landed mutation is progress
        c.after_call("patch", {"path": "x.py", "old_string": "a", "new_string": f"b{i}"},
                     '{"success": true, "diff": "..."}', failed=False)
    assert c.halt_decision is None
    assert c.before_call("terminal", _PYTEST).allows_execution


def test_pure_replay_with_no_intervening_change_is_still_blocked():
    c = _HARD()
    for _ in range(5):
        _run_red(c)
    d = c.before_call("terminal", _PYTEST)
    assert d.action == "block" and d.code == "repeated_exact_failure_block"


def test_intervening_mutation_resets_the_replay_streak_only_once():
    # 4 reds, one edit, then 4 reds with NO edit: the second run of 4 is a
    # fresh streak, and the 5th unchanged retry after it is blocked.
    c = _HARD()
    for _ in range(4):
        _run_red(c)
    c.after_call("write_file", {"path": "x.py", "content": "y"}, '{"bytes_written": 1}', failed=False)
    for _ in range(5):
        assert c.before_call("terminal", _PYTEST).allows_execution
        c.after_call("terminal", _PYTEST, _RED, failed=True)
    assert c.before_call("terminal", _PYTEST).action == "block"


def test_distinct_failing_terminal_commands_warn_but_never_halt():
    # A diagnostic sweep: grep with no matches, missing binaries, red builds.
    c = _HARD()
    for i in range(12):
        args = {"command": f"grep -q needle{i} haystack.txt"}
        d = c.after_call("terminal", args, _RED, failed=True)
        assert not d.should_halt, f"same_tool halt on distinct command #{i + 1}"
    assert c.halt_decision is None
    # ...while a non-tolerant tool failing 8 distinct ways still halts.
    c2 = _HARD()
    last = None
    for i in range(8):
        last = c2.after_call("send_message", {"to": f"u{i}"}, '{"error": "no route"}', failed=True)
    assert last.should_halt and last.code == "same_tool_failure_halt"


def test_browser_retry_after_action_is_not_a_replay():
    c = _HARD()
    nav = {"url": "https://example.test/app"}
    for _ in range(8):
        assert c.before_call("browser_navigate", nav).allows_execution
        c.after_call("browser_navigate", nav, '{"error": "timeout"}', failed=True)
        c.after_call("browser_click", {"selector": "#retry"}, '{"ok": true}', failed=False)
    assert c.halt_decision is None


def test_supervised_task_platforms_keep_warning_only_default():
    for platform in ("subagent", "api_server", "cli"):
        cfg = ToolCallGuardrailConfig.from_mapping({}, platform=platform)
        assert cfg.hard_stop_enabled is False, platform
    for platform in ("telegram", "discord", "cron", "kanban"):
        cfg = ToolCallGuardrailConfig.from_mapping({}, platform=platform)
        assert cfg.hard_stop_enabled is True, platform



def test_browser_interact_waits_are_observational_for_no_progress_guard():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            no_progress_warn_after=2,
            no_progress_block_after=2,
        )
    )
    args = {"action": "wait_text", "text": "Confirmed"}
    result = '{"success":true,"action":"wait_text","waited_for":"text","text":"Confirmed"}'

    assert controller.before_call("browser_interact", args).action == "allow"
    assert controller.after_call("browser_interact", args, result, failed=False).action == "allow"
    assert controller.before_call("browser_interact", args).action == "allow"
    warned = controller.after_call("browser_interact", args, result, failed=False)

    assert warned.action == "warn"
    assert warned.code == "idempotent_no_progress_warning"
    blocked = controller.before_call("browser_interact", args)
    assert blocked.action == "block"
    assert blocked.code == "idempotent_no_progress_block"


def test_browser_interact_page_actions_remain_mutations_for_no_progress_guard():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            no_progress_warn_after=2,
            no_progress_block_after=2,
        )
    )
    args = {"action": "hover", "ref": "@e1"}
    result = '{"success":true,"action":"hover","hovered":"@e1"}'

    for _ in range(3):
        assert controller.before_call("browser_interact", args).action == "allow"
        assert controller.after_call("browser_interact", args, result, failed=False).action == "allow"


# --- F5.5 identical-failure cap (opt-in, arms the interactive warn-only default) ---

_CAP_CFG = {"identical_failure_cap": True, "identical_failure_cap_after": 5}


def _cap_controller(**cfg_overrides) -> ToolCallGuardrailController:
    cfg = dict(_CAP_CFG)
    cfg.update(cfg_overrides)
    return ToolCallGuardrailController(ToolCallGuardrailConfig.from_mapping(cfg, platform="cli"))


def _fail_identically(controller, tool_name, args, result, count):
    for _ in range(count):
        assert controller.before_call(tool_name, args).action == "allow"
        controller.after_call(tool_name, args, result, failed=True)


def test_identical_failure_cap_default_off_keeps_interactive_warn_only_behaviour():
    controller = ToolCallGuardrailController(ToolCallGuardrailConfig.from_mapping({}, platform="cli"))
    args = {"path": "/tmp/missing.txt"}

    for _ in range(100):
        assert controller.before_call("read_file", args).action == "allow"
        controller.after_call("read_file", args, '{"error": "File not found"}', failed=True)

    assert controller.before_call("read_file", args).action == "allow"


def test_identical_failure_cap_blocks_nth_consecutive_identical_failure_with_real_error():
    controller = _cap_controller()
    args = {"path": "/tmp/missing.txt"}
    err = '{"error": "File not found: /tmp/missing.txt [similar: none]"}'

    _fail_identically(controller, "read_file", args, err, 5)

    blocked = controller.before_call("read_file", args)
    assert blocked.action == "block"
    assert blocked.code == "identical_failure_cap_block"
    assert "File not found: /tmp/missing.txt" in blocked.message
    assert "5 times" in blocked.message


def test_identical_failure_cap_allows_first_n_and_blocks_the_rest():
    controller = _cap_controller()
    args = {"path": "/tmp/missing.txt"}
    err = '{"error": "File not found"}'
    allowed = blocked = 0
    for _ in range(100):
        decision = controller.before_call("read_file", args)
        if decision.allows_execution:
            allowed += 1
            controller.after_call("read_file", args, err, failed=True)
        else:
            blocked += 1
    assert allowed == 5
    assert blocked == 95


def test_identical_failure_cap_resets_on_different_args():
    controller = _cap_controller()
    err = '{"error": "File not found"}'
    _fail_identically(controller, "read_file", {"path": "/tmp/a.txt"}, err, 5)
    # Different args each call: separate signatures; 100 distinct paths never accumulate.
    for i in range(100):
        args = {"path": f"/tmp/var-{i}.txt"}
        assert controller.before_call("read_file", args).action == "allow"
        controller.after_call("read_file", args, err, failed=True)
        assert controller.before_call("read_file", args).action == "allow"


def test_identical_failure_cap_resets_when_error_changes():
    controller = _cap_controller()
    args = {"path": "/tmp/flaky.txt"}
    _fail_identically(controller, "read_file", args, '{"error": "locked"}', 4)
    # Different error: the streak restarts at 1.
    controller.before_call("read_file", args)
    controller.after_call("read_file", args, '{"error": "not found"}', failed=True)
    _fail_identically(controller, "read_file", args, '{"error": "not found"}', 3)
    assert controller.before_call("read_file", args).action == "allow"  # 1 + 3 = 4 < 5
    controller.before_call("read_file", args)
    controller.after_call("read_file", args, '{"error": "not found"}', failed=True)  # 5th
    assert controller.before_call("read_file", args).action == "block"


def test_identical_failure_cap_resets_on_success():
    controller = _cap_controller()
    args = {"path": "/tmp/late.txt"}
    err = '{"error": "File not found"}'
    _fail_identically(controller, "read_file", args, err, 5)
    # The file appears: the call succeeds; the streak is gone.
    controller.before_call("read_file", args)
    controller.after_call("read_file", args, '{"content": "1|hi", "total_lines": 1}', failed=False)
    for _ in range(3):
        assert controller.before_call("read_file", args).action == "allow"
        controller.after_call("read_file", args, err, failed=True)
    assert controller.before_call("read_file", args).action == "allow"


def test_identical_failure_cap_resets_on_filesystem_state_change(tmp_path):
    controller = _cap_controller()
    target = tmp_path / "external.txt"
    args = {"path": str(target)}
    err_text = '{"error": "File not found: external.txt"}'
    for _ in range(5):
        assert controller.before_call("read_file", args).action == "allow"
        controller.after_call("read_file", args, err_text, failed=True)
    assert controller.before_call("read_file", args).action == "block"
    # An EXTERNAL change (not the agent) creates the file: the next retry must pass.
    target.write_text("created externally", encoding="utf-8")
    decision = controller.before_call("read_file", args)
    assert decision.action == "allow"
    # The same identical failure afterwards starts a fresh streak of 1.
    controller.after_call("read_file", args, err_text, failed=True)
    assert controller.before_call("read_file", args).action == "allow"


def test_identical_failure_cap_shadow_counts_without_blocking():
    controller = _cap_controller(identical_failure_cap_shadow=True)
    args = {"path": "/tmp/missing.txt"}
    err = '{"error": "File not found"}'
    for _ in range(100):
        decision = controller.before_call("read_file", args)
        assert decision.action == "allow"
        controller.after_call("read_file", args, err, failed=True)
    # Shadow never blocked; the streak kept counting (record intact).
    record = controller._identical_failure_cap[ToolCallSignature.from_call("read_file", args)]
    assert record[1] == 100


def test_identical_failure_cap_threshold_is_configurable():
    controller = _cap_controller(identical_failure_cap_after=2)
    args = {"path": "/tmp/missing.txt"}
    err = '{"error": "File not found"}'
    _fail_identically(controller, "read_file", args, err, 2)
    assert controller.before_call("read_file", args).action == "block"


def test_identical_failure_cap_arms_even_when_hard_stop_disabled():
    # The F5.5 point: interactive default has hard_stop_enabled=False; the cap still fires.
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig.from_mapping(
            {"identical_failure_cap": True, "identical_failure_cap_after": 5}, platform="cli"
        )
    )
    assert controller.config.hard_stop_enabled is False
    args = {"path": "/tmp/missing.txt"}
    err = '{"error": "File not found"}'
    _fail_identically(controller, "read_file", args, err, 5)
    assert controller.before_call("read_file", args).action == "block"


def test_identical_failure_cap_coexists_with_hard_stop_enabled():
    # With hard stops on, both detectors are armed; whichever threshold hits first wins.
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig.from_mapping(
            {"identical_failure_cap": True, "identical_failure_cap_after": 5, "hard_stop_enabled": True},
            platform="cli",
        )
    )
    args = {"path": "/tmp/missing.txt"}
    err = '{"error": "File not found"}'
    _fail_identically(controller, "read_file", args, err, 5)
    decision = controller.before_call("read_file", args)
    assert decision.action == "block"
    assert decision.code in {"identical_failure_cap_block", "repeated_exact_failure_block"}
