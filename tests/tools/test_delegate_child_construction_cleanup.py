"""The child builder owns every fully initialized child until it returns it."""

import contextlib
import json
from types import SimpleNamespace

import pytest

from tools import delegate_tool as dt


@contextlib.contextmanager
def real_parent(tmp_path, monkeypatch, cap="0"):
    from hermes_state import SessionDB
    from hermes_state_registry import release, stats
    from run_agent import AIAgent

    monkeypatch.setenv("STARDUST_HOME", str(tmp_path))
    monkeypatch.delenv("STARDUST_IGNORE_USER_CONFIG", raising=False)
    (tmp_path / "config.yaml").write_text(
        f"delegation:\n  compression_threshold_tokens: {cap}\n  worktree_isolation: false\n"
        "memory:\n  memory_enabled: false\n  user_profile_enabled: false\n", encoding="utf-8",
    )
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("construction-parent", source="test")
    parent = AIAgent(
        session_id="construction-parent", session_db=db, api_key="local-test-only",
        provider="openrouter", base_url="http://127.0.0.1:1/v1", model="test-model",
        api_mode="chat_completions", quiet_mode=True, skip_context_files=True,
        skip_memory=True, save_trajectories=False, enabled_toolsets=["file"],
    )
    baseline = stats()
    children = []
    original_init = AIAgent.__init__

    def observe(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        if kwargs.get("parent_session_id") == parent.session_id:
            children.append(self)

    monkeypatch.setattr(AIAgent, "__init__", observe)
    try:
        yield SimpleNamespace(parent=parent, db=db, children=children, baseline=baseline)
    finally:
        # One acquired child per case. Reproduce the baseline without leaving its leak behind.
        for child in children:
            if child.client is not None:
                child.close()
            release(child._session_db)  # no-op if production cleanup already released it
        parent.close()
        db.close()


def assert_reclaimed(case):
    from hermes_state_registry import stats

    assert len(case.children) == 1
    child = case.children[0]
    print("child construction census", {"before": case.baseline, "after": stats(),
                                       "client_live": child.client is not None,
                                       "owns_db": child._owns_session_db,
                                       "attached": child in case.parent._active_children})
    assert child.client is None
    assert child._owns_session_db is False
    assert child not in case.parent._active_children
    assert stats() == case.baseline
    assert case.db.get_session(case.parent.session_id) is not None


@pytest.mark.parametrize("cap,error_type", [(".nan", ValueError), (".inf", OverflowError)])
@pytest.mark.parametrize("entry", ["delegate", "lifecycle"])
def test_real_nonfinite_config_failure_releases_initialized_child(tmp_path, monkeypatch, cap, error_type, entry):
    from agent.subagent_lifecycle import SubagentLaunchRequest, SubagentLifecycleService

    with real_parent(tmp_path, monkeypatch, cap) as case:
        if entry == "delegate" and error_type is ValueError:
            result = json.loads(dt.delegate_task(goal="Inspect isolated component", parent_agent=case.parent))
            assert "nan" in result["error"].lower()
        else:
            with pytest.raises(error_type):
                if entry == "delegate":
                    dt.delegate_task(goal="Inspect isolated component", parent_agent=case.parent)
                else:
                    SubagentLifecycleService(lambda: case.parent).launch(SubagentLaunchRequest(
                        goal="Inspect isolated component", correlation_id="construction-fault",
                    ))
        assert_reclaimed(case)


def test_early_postinit_failure_already_owns_child_database(tmp_path, monkeypatch):
    with real_parent(tmp_path, monkeypatch) as case:
        def fail_cache(_child):
            raise RuntimeError("controlled early postinit failure")

        monkeypatch.setattr(dt, "_apply_child_cache_ttl", fail_cache)
        with pytest.raises(RuntimeError, match="controlled early postinit failure"):
            dt._build_child_preserving_parent_tools(
                task_index=0, goal="Inspect component", context=None, toolsets=None, model=None,
                max_iterations=2, task_count=1, parent_agent=case.parent,
            )
        assert_reclaimed(case)


def test_cancel_from_start_hook_reclaims_attached_child(tmp_path, monkeypatch):
    with real_parent(tmp_path, monkeypatch) as case:
        def cancel_start(event, **_kwargs):
            if event == "subagent_start":
                assert case.children[0] in case.parent._active_children
                raise KeyboardInterrupt("controlled lifecycle-hook cancellation")

        monkeypatch.setattr("hermes_cli.lifecycle.invoke_hook", cancel_start)
        with pytest.raises(KeyboardInterrupt, match="controlled lifecycle-hook cancellation"):
            dt._build_child_preserving_parent_tools(
                task_index=0, goal="Inspect component", context=None, toolsets=None, model=None,
                max_iterations=2, task_count=1, parent_agent=case.parent,
            )
        assert_reclaimed(case)


def test_successful_construction_transfers_open_child(tmp_path, monkeypatch):
    from hermes_state_registry import stats
    from tools.delegate_tool_child_run import _detach_child

    with real_parent(tmp_path, monkeypatch) as case:
        child = dt._build_child_preserving_parent_tools(
            task_index=0, goal="Inspect component", context=None, toolsets=None, model=None,
            max_iterations=2, task_count=1, parent_agent=case.parent,
        )
        assert case.children == [child]
        assert child.client is not None
        assert child._owns_session_db is True
        assert child in case.parent._active_children
        assert stats()["total_refcounts"] == case.baseline["total_refcounts"] + 1
        _detach_child(case.parent, child)
        child.close()
        assert_reclaimed(case)
