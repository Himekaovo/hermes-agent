from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.modules.setdefault("fire", types.SimpleNamespace(Fire=lambda *a, **k: None))
sys.modules.setdefault("firecrawl", types.SimpleNamespace(Firecrawl=object))
sys.modules.setdefault("fal_client", types.SimpleNamespace())

from run_agent import AIAgent


class _FakeOpenAI:
    def __init__(self, **kw):
        self.api_key = kw.get("api_key", "test")
        self.base_url = kw.get("base_url", "http://test")

    def close(self):
        pass


def _make_agent(monkeypatch, **kwargs):
    monkeypatch.setattr(
        "run_agent.get_tool_definitions",
        lambda **_kw: [],
    )
    monkeypatch.setattr("run_agent.check_toolset_requirements", lambda: {})
    monkeypatch.setattr("run_agent.OpenAI", _FakeOpenAI)
    params = dict(
        api_key="test-key",
        base_url="https://openrouter.ai/api/v1",
        provider="openrouter",
        model="test-model",
        max_iterations=4,
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
        save_trajectories=False,
        session_id="sess-safety",
    )
    params.update(kwargs)
    return AIAgent(**params)


def test_agent_init_composes_builtin_safety_hooks_before_explicit_callbacks(monkeypatch):
    explicit_calls = []

    def explicit_hook(**kwargs):
        explicit_calls.append(kwargs["user_message"])
        return {"action": "allow", "context": "explicit ok"}

    original = {"pre_llm_call": [explicit_hook]}
    agent = _make_agent(monkeypatch, hook_overrides=original)

    assert original == {"pre_llm_call": [explicit_hook]}
    assert len(agent.hook_overrides["pre_llm_call"]) == 2
    assert agent.hook_overrides["pre_llm_call"][0] is not explicit_hook
    assert agent.hook_overrides["pre_llm_call"][1] is explicit_hook


def test_pre_llm_block_uses_instance_hook_scope_and_skips_model_transport(monkeypatch):
    seen = {}

    def explicit_block(**kwargs):
        seen.update(kwargs)
        return {
            "hook": "instance-blocker",
            "event": "pre_llm_call",
            "action": "block",
            "reason_code": "instance_blocked",
            "risk_level": "high",
            "message": "Blocked before transport.",
            "metadata": {"source": "test"},
        }

    agent = _make_agent(
        monkeypatch,
        platform="cli",
        parent_session_id="parent-session",
        hook_overrides={"pre_llm_call": [explicit_block]},
    )
    agent.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=MagicMock(side_effect=AssertionError("transport should not run"))))
    )

    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("hello safety", task_id="task-1")

    assert seen["agent_id"]
    assert seen["execution_kind"] == "subagent"
    assert seen["session_id"] == "sess-safety"
    assert seen["task_id"] == "task-1"
    assert seen["turn_id"]
    assert seen["parent_session_id"] == "parent-session"
    assert seen["user_message"] == "hello safety"

    assert result["completed"] is False
    assert result["failed"] is True
    assert result["api_calls"] == 0
    assert result["safety_blocked"] is True
    assert result["turn_exit_reason"] == "safety_blocked"
    assert result["final_response"] == "Blocked before transport."
    assert result["safety_results"][-1]["reason_code"] == "instance_blocked"
    assert result["messages"][-1] == {
        "role": "assistant",
        "content": "Blocked before transport.",
    }
    assert agent.client.chat.completions.create.call_count == 0
