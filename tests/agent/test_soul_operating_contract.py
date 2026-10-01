"""A custom SOUL.md replaces the persona, not the operating contract.

Field case 2026-10-01: a hand-written SOUL.md silently dropped the whole default identity, including
the part that says what counts as authorization and how intent maps to action, so the assistant asked
before routine actions and stopped at "I can't" instead of finding another way.
"""

from types import SimpleNamespace
from unittest.mock import patch

from agent.prompt_builder import (
    ASSISTANT_OPERATING_CONTRACT,
    DEFAULT_AGENT_IDENTITY,
    OPENAI_MODEL_EXECUTION_GUIDANCE,
)
from agent.system_prompt import build_system_prompt_parts
from hermes_cli.default_soul import DEFAULT_SOUL_MD

_CUSTOM_SOUL = "You are Jarvis. Speak Chinese, be warm and brief."


def _agent(**overrides):
    base = dict(
        load_soul_identity=False,
        skip_context_files=False,
        valid_tool_names=[],
        _task_completion_guidance=True,
        _tool_use_enforcement=False,
        _environment_probe=False,
        _kanban_worker_guidance="",
        _memory_store=None,
        _memory_manager=None,
        model="",
        provider="",
        platform="",
        pass_session_id=False,
        session_id="",
        _emit_status=lambda *_args, **_kwargs: None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _stable_with_soul(tmp_path, monkeypatch, soul_text, **agent_overrides):
    """Stable tier built through the real SOUL.md load path (read, legacy strip, scan, truncate)."""
    home = tmp_path / "hermes-home"
    home.mkdir()
    (home / "SOUL.md").write_text(soul_text, encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    with (
        patch("agent.prompt_builder.build_environment_hints", return_value=""),
        patch("agent.prompt_builder.build_context_files_prompt", return_value=""),
    ):
        return build_system_prompt_parts(_agent(**agent_overrides))["stable"]


def test_custom_soul_keeps_the_operating_contract(tmp_path, monkeypatch):
    stable = _stable_with_soul(tmp_path, monkeypatch, _CUSTOM_SOUL)

    assert _CUSTOM_SOUL in stable
    assert "You are Stardust" not in stable  # the custom persona still replaces the default one
    assert stable.count(ASSISTANT_OPERATING_CONTRACT) == 1
    assert stable.index(_CUSTOM_SOUL) < stable.index(ASSISTANT_OPERATING_CONTRACT)


def test_seeded_default_soul_carries_the_contract_once(tmp_path, monkeypatch):
    stable = _stable_with_soul(tmp_path, monkeypatch, DEFAULT_SOUL_MD)

    assert ASSISTANT_OPERATING_CONTRACT in DEFAULT_AGENT_IDENTITY
    assert stable.count(ASSISTANT_OPERATING_CONTRACT) == 1
    assert "# Operating defaults" not in stable


def test_tool_sessions_are_told_to_find_a_way_instead_of_handing_work_back(tmp_path, monkeypatch):
    stable = _stable_with_soul(tmp_path, monkeypatch, _CUSTOM_SOUL, valid_tool_names={"terminal"})

    assert "install or enable it yourself" in stable
    assert "try another" in stable
    assert "only for what only they can give" in stable
    assert "not instructions, a script, or a demo" in stable


def test_act_dont_ask_examples_include_everyday_personal_tasks():
    examples = OPENAI_MODEL_EXECUTION_GUIDANCE.split("<act_dont_ask>", 1)[1].split("</act_dont_ask>", 1)[0]

    assert "Downloads" in examples
