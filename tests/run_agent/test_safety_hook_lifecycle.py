from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.modules.setdefault("fire", types.SimpleNamespace(Fire=lambda *a, **k: None))
sys.modules.setdefault("firecrawl", types.SimpleNamespace(Firecrawl=object))
sys.modules.setdefault("fal_client", types.SimpleNamespace())

from agent.turn_finalizer import finalize_turn
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


class _FinalizerAgent:
    def __init__(self):
        self.max_iterations = 4
        self.iteration_budget = SimpleNamespace(remaining=3, used=1, max_total=4)
        self.quiet_mode = True
        self.model = "test-model"
        self.provider = "openrouter"
        self.base_url = "https://openrouter.ai/api/v1"
        self.session_id = "sess-finalizer"
        self.platform = "cli"
        self.context_compressor = SimpleNamespace(last_prompt_tokens=0)
        self.session_input_tokens = 0
        self.session_output_tokens = 0
        self.session_cache_read_tokens = 0
        self.session_cache_write_tokens = 0
        self.session_reasoning_tokens = 0
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self.session_estimated_cost_usd = 0
        self.session_cost_status = "unknown"
        self.session_cost_source = "test"
        self._tool_guardrail_halt_decision = None
        self._interrupt_message = None
        self._response_was_previewed = False
        self._skill_nudge_interval = 0
        self._iters_since_skill = 0
        self.valid_tool_names = []
        self._parent_session_id = None
        self.persist_calls = []
        self._stream_callback = None

    def _handle_max_iterations(self, messages, api_call_count):
        raise AssertionError("not expected")

    def _emit_status(self, *_args, **_kwargs):
        pass

    def _safe_print(self, *_args, **_kwargs):
        pass

    def _save_trajectory(self, *_args, **_kwargs):
        pass

    def _cleanup_task_resources(self, *_args, **_kwargs):
        pass

    def _drop_trailing_empty_response_scaffolding(self, messages):
        pass

    def _persist_session(self, messages, conversation_history):
        self.persist_calls.append([dict(message) for message in messages])

    def _file_mutation_verifier_enabled(self):
        return False

    def _turn_completion_explainer_enabled(self):
        return False

    def _drain_pending_steer(self):
        return None

    def clear_interrupt(self):
        pass

    def _sync_external_memory_for_turn(self, **_kwargs):
        pass


def _finalize(agent, *, final_response, turn_id="turn-post-block", task_id="task-post-block"):
    return finalize_turn(
        agent,
        final_response=final_response,
        api_call_count=1,
        interrupted=False,
        failed=False,
        messages=[
            {"role": "user", "content": "say the secret"},
            {"role": "assistant", "content": final_response},
        ],
        conversation_history=[],
        effective_task_id=task_id,
        turn_id=turn_id,
        user_message="say the secret",
        original_user_message="say the secret",
        _should_review_memory=False,
        _turn_exit_reason="text_response(finish_reason=stop)",
    )


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
    assert [cb.__name__ for cb in agent.hook_overrides["post_llm_call"]] == [
        "_post_llm_call"
    ]
    assert [cb.__name__ for cb in agent.hook_overrides["on_session_end"]] == [
        "_on_session_end"
    ]


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


def test_post_llm_block_replaces_user_visible_response_without_retry(monkeypatch):
    import agent.safety_hooks as safety_hooks

    def fake_invoke_hook(name, **kwargs):
        if name == "transform_llm_output":
            return []
        if name == "post_llm_call":
            return [safety_hooks.run_safety_checks(name, kwargs)]
        if name == "on_session_end":
            callback = safety_hooks.build_safety_hook_overrides()["on_session_end"][0]
            return [callback(**kwargs)]
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", fake_invoke_hook)
    agent = _FinalizerAgent()

    result = _finalize(agent, final_response="my api key is sk-test-secret")

    assert result["safety_blocked"] is True
    assert result["failed"] is True
    assert result["turn_exit_reason"] == "safety_blocked"
    assert result["final_response"] != "my api key is sk-test-secret"
    assert "sk-test-secret" not in result["final_response"]
    assert result["safety_results"][0]["hook"] == "egress-inspector"
    assert result["messages"][-1]["role"] == "assistant"
    assert "sk-test-secret" not in json.dumps(result["messages"][-1])
    assert agent.persist_calls[-1][-1]["content"] == result["final_response"]


def test_finalize_turn_runs_session_archiver_fail_open(monkeypatch, tmp_path):
    import agent.safety_hooks as safety_hooks

    captured = {"calls": 0}

    def fake_emit(candidate):
        captured["calls"] += 1
        captured["candidate"] = candidate
        raise RuntimeError("governance offline")

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(safety_hooks, "_emit_memory_governance_candidate", fake_emit, raising=False)
    callback = safety_hooks.build_safety_hook_overrides()["on_session_end"][0]
    result = callback(
        agent_id="agent-1",
        execution_kind="interactive",
        session_id="sess-end",
        task_id="task-end",
        turn_id="turn-end",
        completed=True,
        interrupted=False,
        conversation_history=[
            {"role": "user", "content": "token=sk-test-secret"},
            {"role": "assistant", "content": "Thanks."},
        ],
    )

    assert captured["calls"] == 1
    assert result["hook"] == "session-archiver"
    assert result["metadata"]["memory_candidate_status"] == "governance_missing"
