from types import SimpleNamespace


def test_clone_hook_overrides_copies_callback_lists():
    from tools.delegate_tool import _clone_hook_overrides

    first = lambda **kwargs: "first"
    overrides = {"on_session_start": [first]}

    cloned = _clone_hook_overrides(overrides)

    assert cloned == overrides
    assert cloned is not overrides
    assert cloned["on_session_start"] is not overrides["on_session_start"]


def test_child_hook_overrides_include_fresh_safety_callbacks(monkeypatch, tmp_path):
    from agent.safety_hooks import build_safety_hook_overrides
    from tools.delegate_tool import _build_child_agent

    explicit = lambda **kwargs: "explicit"
    parent_hooks = build_safety_hook_overrides()
    parent_hooks["pre_llm_call"] = [*parent_hooks["pre_llm_call"], explicit]

    captured = {}

    class FakeChild:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.session_id = "child-session"
            self._session_init_model_config = {}

    parent = SimpleNamespace(
        _delegate_depth=0,
        enabled_toolsets=["terminal"],
        disabled_toolsets=[],
        session_id="parent-session",
        hook_overrides=parent_hooks,
        model="gpt-test",
        provider="openai",
        base_url="https://example.test",
        api_mode="responses",
        reasoning_config=None,
        prefill_messages=None,
        _fallback_chain=None,
        request_overrides={},
        max_tokens=None,
        _session_db=None,
        _print_fn=None,
        _current_turn_id="turn-1",
        _subagent_id=None,
        terminal_cwd=str(tmp_path),
    )

    monkeypatch.setattr("run_agent.AIAgent", FakeChild)
    monkeypatch.setattr("tools.delegate_tool._load_config", lambda: {})

    _build_child_agent(
        task_index=0,
        goal="Inspect safely",
        context=None,
        toolsets=None,
        model=None,
        max_iterations=3,
        task_count=1,
        parent_agent=parent,
    )

    child_hooks = captured["hook_overrides"]

    assert captured["platform"] == "subagent"
    assert captured["parent_session_id"] == "parent-session"
    assert captured["skip_memory"] is True
    assert child_hooks["pre_llm_call"] is not parent_hooks["pre_llm_call"]
    assert child_hooks["pre_llm_call"][0] is not parent_hooks["pre_llm_call"][0]
    assert child_hooks["post_llm_call"][0] is not parent_hooks["post_llm_call"][0]
    assert child_hooks["on_session_end"][0] is not parent_hooks["on_session_end"][0]
    assert child_hooks["pre_llm_call"][-1] is explicit

    child_hooks["pre_llm_call"].append(lambda **kwargs: "child-only")

    assert len(parent_hooks["pre_llm_call"]) == 2
    assert len(child_hooks["pre_llm_call"]) == 3
