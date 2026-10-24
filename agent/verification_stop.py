"""Turn-end verification guard for coding edits. Policy-only: it never runs
checks itself, it turns the passive verification ledger into a bounded follow-up
when the model tries to finish right after editing code without fresh evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

from utils import is_truthy_value


_MAX_CHANGED_PATHS_IN_NUDGE = 8

# Prose/data extensions and extension-less prose filenames (case-insensitive) with
# no verifiable runtime behavior: a turn touching ONLY these suppresses the nudge
# (a SKILL.md/README edit must never demand a /tmp verification script).
_NON_CODE_VERIFY_EXTENSIONS = frozenset(
    {".md", ".markdown", ".mdx", ".rst", ".txt", ".text", ".adoc", ".asciidoc", ".org", ".log", ".csv", ".tsv"}
)
_NON_CODE_VERIFY_FILENAMES = frozenset(
    {"license", "licence", "notice", "authors", "contributors", "changelog", "codeowners"}
)

_FALSY_TOKENS = {"0", "false", "no", "off"}
_TRUTHY_TOKENS = {"1", "true", "yes", "on"}


def _is_non_code_path(raw: str) -> bool:
    """True when a changed path is documentation/prose with nothing to verify."""
    try:
        p = Path(str(raw))
    except Exception:
        return False
    suffix = p.suffix.lower()
    return suffix in _NON_CODE_VERIFY_EXTENSIONS or (not suffix and p.name.lower() in _NON_CODE_VERIFY_FILENAMES)


def _session_is_messaging_surface() -> bool:
    """Whether this turn is delivered over a human messaging channel. An
    unreachable gateway package means no messaging channel (verify-on-stop stays on)."""
    try:
        from gateway.session_context import session_is_messaging_surface

        return session_is_messaging_surface()
    except Exception:
        return False


def verify_on_stop_enabled(config: dict[str, Any] | None = None) -> bool:
    """Return whether edit -> verify-before-finish behavior is enabled.

    Precedence: ``STARDUST_VERIFY_ON_STOP`` env var, then ``agent.verify_on_stop``
    config. ``DEFAULT_CONFIG`` uses ``"auto"``: ON for interactive coding
    surfaces and programmatic callers, OFF for messaging surfaces. A bool forces
    the behavior everywhere; missing or unrecognized values fail closed to OFF.
    """
    env = os.environ.get("STARDUST_VERIFY_ON_STOP")
    if env is not None:
        return env.strip().lower() not in _FALSY_TOKENS
    if config is None:
        try:
            from hermes_cli.config import load_config_readonly

            config = load_config_readonly()
        except Exception:
            config = {}
    agent_cfg = (config or {}).get("agent") if isinstance(config, dict) else None
    cfg_val = agent_cfg.get("verify_on_stop") if isinstance(agent_cfg, dict) else None
    if isinstance(cfg_val, bool):
        return cfg_val
    token = cfg_val.strip().lower() if isinstance(cfg_val, str) else ""
    if token == "auto":
        return not _session_is_messaging_surface()
    return token in _TRUTHY_TOKENS


def _load_agent_config() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly()
    except Exception:
        config = {}
    agent_cfg = (config or {}).get("agent") if isinstance(config, dict) else None
    return agent_cfg if isinstance(agent_cfg, dict) else {}


def verify_on_stop_messaging_enabled(config: dict[str, Any] | None = None) -> bool:
    """Return the ``agent.verify_on_stop_messaging`` opt-in flag (default OFF).

    When ON, a final response on a messaging surface that asserts a completed
    action ("done/fixed/完成/搞定/...") must be backed by a successful tool
    result from this turn, else the turn continues once with a nudge. Mirrors
    the ``memory.topic_provenance_enabled`` read pattern: safe default, no
    config_defaults change, flag OFF = byte-identical behaviour.
    """
    agent_cfg = config.get("agent") if isinstance(config, dict) else None
    if not isinstance(agent_cfg, dict):
        agent_cfg = _load_agent_config()
    return is_truthy_value(agent_cfg.get("verify_on_stop_messaging"), default=False)


def _candidate_cwds(paths: Iterable[str]) -> list[Path]:
    """Distinct resolved directories (a file's parent) for the edited paths, in order."""
    seen: dict[str, None] = {}
    for raw in filter(None, paths):
        try:
            path = Path(raw).expanduser()
            seen.setdefault(str((path if path.is_dir() else path.parent).resolve()))
        except Exception:
            continue
    return [Path(p) for p in seen]


def _verification_snapshot(
    *, session_id: str | None, changed_paths: list[str]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """``(status, facts)`` for the first edited workspace needing proof, else the
    first recognized workspace when every one is ``passed``."""
    try:
        from agent.coding_context import project_facts_for
        from agent.verification_evidence import verification_status
    except Exception:
        return None

    first_snapshot: tuple[dict[str, Any], dict[str, Any]] | None = None
    for cwd in _candidate_cwds(changed_paths):
        facts = project_facts_for(cwd)
        if not facts:
            continue
        status = verification_status(session_id=session_id, cwd=cwd)
        first_snapshot = first_snapshot or (status, facts)
        if str(status.get("status") or "unverified") != "passed":
            return status, facts
    return first_snapshot


def _unverified_state_key(*, session_id: str | None, changed_paths: list[str]) -> str | None:
    """Identity of the unverified state the model is being told about, ``None`` when nothing needs proof.

    Every edited workspace still lacking proof, each with its last edit and last verification run: another edit or
    another run changes the key, time passing and a repeated turn do not."""
    try:
        from agent.coding_context import project_facts_for
        from agent.verification_evidence import verification_state_marker, verification_status
    except Exception:
        return None

    markers: list[list[Any]] = []
    for cwd in _candidate_cwds(changed_paths):
        if not project_facts_for(cwd):
            continue
        if str(verification_status(session_id=session_id, cwd=cwd).get("status") or "unverified") == "passed":
            continue
        marker = verification_state_marker(session_id=session_id, cwd=cwd)
        markers.append(list(marker) if marker is not None else [str(cwd)])
    if not markers:
        return None
    return hashlib.sha1(json.dumps(sorted(markers), default=str).encode("utf-8")).hexdigest()


def _format_changed_paths(paths: list[str]) -> str:
    lines = [f"- `{path}`" for path in paths[:_MAX_CHANGED_PATHS_IN_NUDGE]]
    if len(paths) > _MAX_CHANGED_PATHS_IN_NUDGE:
        lines.append(f"- ... and {len(paths) - _MAX_CHANGED_PATHS_IN_NUDGE} more")
    return "\n".join(lines)


def _workspace_has_runnable_recipe(root: Any) -> bool:
    """Whether ``stardust verify`` has a runtime recipe here: a saved
    ``.stardust/environment.json`` or a statically detected recipe with a start
    command. Fail-silent and cheap — it only decorates the nudge text."""
    if not root:
        return False
    try:
        from agent.verify.environment import manifest_path
        from agent.verify.recipes import detect_recipe

        root_path = Path(str(root))
        if manifest_path(root_path).is_file():
            return True
        recipe = detect_recipe(root_path)
        return bool(recipe is not None and recipe.start)
    except Exception:
        return False


def _status_detail(status: dict[str, Any]) -> str:
    state = str(status.get("status") or "unverified")
    evidence = status.get("evidence") if isinstance(status.get("evidence"), dict) else None
    if not evidence:
        return state

    command = evidence.get("canonical_command") or evidence.get("command")
    summary = str(evidence.get("output_summary") or "").strip()
    parts = [state]
    if command:
        parts.append(f"last command `{command}`")
    if summary:
        if len(summary) > 1200:
            summary = summary[:1200].rstrip() + "\n... [truncated]"
        parts.append(f"last output:\n{summary}")
    return "\n".join(parts)


def build_verify_on_stop_nudge(
    *, session_id: str | None, changed_paths: Iterable[str], attempts: int=0, max_attempts: int=2,
    nudged_states: set[str] | None = None,
) -> str | None:
    """Return a synthetic follow-up when edited code lacks fresh verification.

    ``attempts`` / ``max_attempts`` bound the follow-ups within one turn. ``nudged_states`` (a set the caller keeps for
    the life of the conversation) bounds them across turns: a state the model was already told about is not told
    again until an edit or a verification run changes it. Without that, every short turn (a user message, a
    background-task notice) restarts the per-turn count and re-asks for proof nothing has changed."""
    # Prose-only turns (markdown, skills, README, LICENSE, ...) have nothing to verify.
    paths = sorted({str(p) for p in changed_paths if p and not _is_non_code_path(p)})
    if not paths or attempts >= max_attempts:
        return None

    snapshot = _verification_snapshot(session_id=session_id, changed_paths=paths)
    if snapshot is None:
        return None
    status, facts = snapshot
    if str(status.get("status") or "unverified") == "passed":
        return None
    if nudged_states is not None:
        state_key = _unverified_state_key(session_id=session_id, changed_paths=paths)
        if state_key is not None:
            if state_key in nudged_states:
                return None
            nudged_states.add(state_key)
    verify_commands = [str(cmd).strip() for cmd in (facts.get("verifyCommands") or []) if str(cmd).strip()]
    has_recipe = _workspace_has_runnable_recipe(facts.get("root"))

    # Optional shipped coding guidance, only paid when this evidence gate fires.
    try:
        from agent.verify_hooks import coding_verify_guidance

        guidance = coding_verify_guidance()
    except Exception:
        guidance = None
    addendum = f"\n\n{guidance}" if guidance else ""

    if verify_commands:
        command_instruction = (
            "Run the relevant verification command now ("
            + ", ".join(f"`{cmd}`" for cmd in verify_commands[:3])
            + (", ..." if len(verify_commands) > 3 else "")
            + "), read any failure, repair the code, and summarize what passed."
        )
        if has_recipe:
            command_instruction += (
                " For a full check including a runtime boot (build + test + "
                "start + readiness), prefer `stardust verify --json` — a passing "
                "run records verification evidence for this workspace."
            )
    elif has_recipe:
        command_instruction = (
            "No canonical test/lint/build command was detected, but the "
            "project has a runnable verification recipe. Run `stardust verify "
            "--json` (detect -> build -> test -> boot -> readiness poll); a "
            "passing run records verification evidence for this workspace. "
            "Read any failure, repair the code, and summarize what passed."
        )
    else:
        temp_dir = os.path.realpath(tempfile.gettempdir())
        command_instruction = (
            "No canonical test/lint/build command was detected. Create a focused "
            f"temporary verification script under `{temp_dir}` using an OS-safe "
            "`tempfile` path with a `hermes-verify-` filename prefix, run it "
            "against the changed behavior, clean it up when possible, and "
            "summarize it explicitly as ad-hoc verification rather than suite "
            "green."
        )

    return (
        "[System: You edited code in this turn, but the workspace does not have "
        "fresh passing verification evidence yet.\n\n"
        f"Verification status: {_status_detail(status)}\n\n"
        f"Changed paths:\n{_format_changed_paths(paths)}\n\n"
        f"{command_instruction} If verification is not possible, explain the "
        "concrete blocker instead of claiming the work is fully verified."
        f"{addendum}]"
    )


# ── messaging completion-claim evidence gate (DL-P2.8e, opt-in) ──────────
#
# Lexicon kept conservative on purpose: a false nudge on a correct answer
# costs a full extra model round and annoys the user (DISCOVERY-8 u17/u18),
# while a missed weak claim ("基本好了") only leaves today's behaviour in
# place. Claims are matched per clause so a negation in one half does not
# silence a real claim in the other ("还没完成X，但Y已删除").

_CLAIM_CN_PATTERNS = (
    r"已(?:经)?(?:完成|修复|搞定|弄好|做好|办好|处理完|写完|改完|删除|删掉|清理完|清理干净|发送|发出去|交付|合入|合并|部署|创建|建好|配置好|设置好|更新|升级|安装|启动|运行|验证|确认|核对|检查|读取|写入|检索|执行)",
    r"(?:完成|搞定|弄好|做好|办好|处理完|修复|收尾|交付|清理|删除|发送|部署|验证|确认)了",
    r"搞定了|都搞定了|全部完成|全部搞定|一切就绪|大功告成|收尾完成",
)

_CLAIM_EN_PATTERN = re.compile(
    r"\b(?:done|fixed|sent|deleted|removed|cleaned|completed|complete|created|updated|"
    r"deployed|installed|configured|verified|confirmed|migrated|merged|"
    r"published|submitted)\b",
    re.IGNORECASE,
)

# Future/negation guards within a clause: "I will fix", "not done yet",
# "还没完成" are intents, not completion claims.
_CLAIM_NEGATIVE_RE = re.compile(
    r"(?:还没|尚未|没有|未)(?:完成|搞定|弄好|做好|修复|处理|发送|删除|清理|验证|确认|做完)"
    r"|待完成|将(?:会?)?(?:完成|修复|处理|发送|删除|清理|验证)"
    r"|打算|准备去做|还没来得及"
    r"|not\s+(?:yet\s+)?done|will\s+(?:fix|do|send|delete|remove|clean|complete)"
    r"|plan(?:ning)?\s+to|to\s+be\s+done|TODO",
    re.IGNORECASE,
)

_CLAIM_CN_RE = re.compile("|".join(_CLAIM_CN_PATTERNS))

# Explicit user opt-out of verification in the CURRENT user turn: the user's
# instruction outranks any automatic nudge (DISCOVERY-8 u17/u18/c03).
_USER_OPT_OUT_RE = re.compile(
    r"不用验证|不用复核|不需要验证|不用复验|无需验证|不必验证"
    r"|不要再?跑|不要重跑|别再?跑|不用再?跑|不要复测|不用复测"
    r"|不用执行|无需执行|不要再?执行|免验证|跳过验证"
    r"|以我的指令为准|以用户指令为准|直接(?:收尾|标记完成|结束|交付)"
    r"|就基于现有结果|基于现有结果|基于(?:现有|上面|刚才)的?结果"
    r"|no\s+need\s+to\s+verify|skip\s+verification"
    r"|don'?t\s+(?:re)?run|do\s+not\s+(?:re)?run"
    r"|based\s+on\s+(?:the\s+)?existing|as-is",
    re.IGNORECASE,
)

# Tool families that never prove an external action happened; a claim backed
# only by these is still unverified (asking a question is not doing the thing).
_NON_EVIDENCE_TOOLS = frozenset({
    "clarify", "todo", "memory", "session_search", "skills", "think",
})

# DL-P2.8f: deterministic post-check note for the iteration-budget summary.
# Wording deliberately avoids the messaging claim lexicon (no 已/完成/done/...)
# so the note itself can never read as a new completion claim, and lists the
# checklist the reader applies instead of asserting anything about the work.
_MAXITER_UNVERIFIED_NOTE = (
    "⚠️ 注意：本回复是在工具调用预算耗尽后生成的收尾总结，其中关于任务结果的表述"
    "未经本轮工具输出核实，请以实际执行记录为准。"
)


_CLAIM_CLAUSE_SPLIT_RE = re.compile(r"[。！!？?；;\n，,]+")


def _response_text(final_response: Any) -> str:
    if isinstance(final_response, str):
        return final_response
    if isinstance(final_response, list):
        return "".join(
            str(p.get("text") or "") for p in final_response if isinstance(p, dict)
        )
    return ""


def _clause_claims_completion(clause: str) -> str | None:
    if _CLAIM_NEGATIVE_RE.search(clause):
        return None
    m = _CLAIM_CN_RE.search(clause) or _CLAIM_EN_PATTERN.search(clause)
    if m:
        return m.group(0)
    stripped = clause.strip()
    # Bare "好了" / "搞好了" asserts completion; "你好/好像/也好" do not (the
    # split already isolated the clause, so a leading 好了 is unambiguous).
    if stripped.startswith("好了") or stripped.startswith("搞好了"):
        return "好了"
    return None


def contains_completion_claim(text: str) -> str | None:
    """First matched completion claim in ``text``, or ``None`` (conservative)."""
    if not text:
        return None
    for clause in _CLAIM_CLAUSE_SPLIT_RE.split(text):
        claim = _clause_claims_completion(clause)
        if claim:
            return claim
    return None


def user_opted_out_of_verification(user_text: Any) -> bool:
    """The current user turn explicitly waived verification/re-runs."""
    return bool(isinstance(user_text, str) and user_text and _USER_OPT_OUT_RE.search(user_text))


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(p.get("text") or "") for p in content if isinstance(p, dict)
        )
    return str(content or "")


def turn_tool_evidence(messages: List[Dict[str, Any]], current_turn_user_idx: Any) -> List[Tuple[str, str]]:
    """``(tool_name, result_text)`` pairs from THIS turn's executed tools.

    The boundary is the current user turn's index (post-compaction reanchor);
    everything after it belongs to this turn's tool loop.
    """
    if not isinstance(current_turn_user_idx, int) or current_turn_user_idx < 0:
        return []
    pairs: list[tuple[str, str]] = []
    for msg in messages[current_turn_user_idx + 1:]:
        if not (isinstance(msg, dict) and msg.get("role") == "tool"):
            continue
        pairs.append((str(msg.get("name") or ""), _tool_result_text(msg.get("content"))))
    return pairs


def _has_evidence(pairs: List[Tuple[str, str]]) -> bool:
    """Evidence = at least one successful non-housekeeping tool result this
    turn (``tool_result_outcome`` reads declared envelope facts only; error,
    cancelled, pending and unknown outcomes are not evidence)."""
    from agent.tool_result_classification import tool_result_outcome
    from utils import safe_json_loads

    for name, text in pairs:
        if name in _NON_EVIDENCE_TOOLS:
            continue
        outcome = tool_result_outcome(name, safe_json_loads(text) if text else None)
        if outcome == "success":
            return True
    return False


def messaging_claim_gate_nudge(
    *, final_response: Any, messages: List[Dict[str, Any]], current_turn_user_idx: Any,
    user_message: Any, attempts: int = 0, config: dict[str, Any] | None = None,
) -> Optional[str]:
    """Opt-in messaging evidence gate: nudge (once) when the final response
    asserts a completed action that no successful tool result this turn backs.

    Returns ``None`` — no nudge — unless every condition holds: the
    ``agent.verify_on_stop_messaging`` flag is ON, the turn is on a messaging
    surface, the response contains a completion claim, the user did not
    explicitly waive verification this turn, no successful tool result exists,
    and no verification nudge was issued yet this turn (shared counter).
    """
    if attempts >= 1:
        return None
    try:
        if not verify_on_stop_messaging_enabled(config):
            return None
        if not _session_is_messaging_surface():
            return None
        claim = contains_completion_claim(_response_text(final_response))
        if claim is None:
            return None
        if user_opted_out_of_verification(user_message):
            return None
        if _has_evidence(turn_tool_evidence(messages, current_turn_user_idx)):
            return None
        return (
            "[System: 若用户本轮已明说不必验证/不必重跑，则以用户指令为准，直接保留你原来的回答。]\n"
            "Your final answer claims an action is complete (\"" + claim[:80] + "\"), but no "
            "successful tool result from this turn backs it. Either run the real "
            "tool/command now and report its actual output, or state plainly which "
            "parts are unverified. Do not repeat the completion claim without evidence."
        )
    except Exception:
        return None


def max_iterations_unverified_note(
    *, summary_text: Any, messages: List[Dict[str, Any]], current_turn_user_idx: Any,
    user_message: Any, config: dict[str, Any] | None = None,
) -> Optional[str]:
    """Opt-in deterministic post-check for the iteration-budget summary (DL-P2.8f).

    The max-iterations summary is composed after the loop has already exited,
    so it never passes the stop gate. When ``agent.verify_on_stop_messaging``
    is ON and the summary asserts a completed action that no successful tool
    result from this turn backs, return the plain unverified note to append.
    Same flag, lexicon, evidence and user-opt-out rules as
    :func:`messaging_claim_gate_nudge`; no model call, no extra iteration. Any
    failure returns ``None`` (summary unchanged).
    """
    try:
        if not verify_on_stop_messaging_enabled(config):
            return None
        if not _session_is_messaging_surface():
            return None
        if contains_completion_claim(_response_text(summary_text)) is None:
            return None
        if user_opted_out_of_verification(user_message):
            return None
        if _has_evidence(turn_tool_evidence(messages, current_turn_user_idx)):
            return None
        return _MAXITER_UNVERIFIED_NOTE
    except Exception:
        return None


__all__ = [
    "build_verify_on_stop_nudge",
    "verify_on_stop_enabled",
    "verify_on_stop_messaging_enabled",
    "contains_completion_claim",
    "user_opted_out_of_verification",
    "turn_tool_evidence",
    "messaging_claim_gate_nudge",
    "max_iterations_unverified_note",
]
