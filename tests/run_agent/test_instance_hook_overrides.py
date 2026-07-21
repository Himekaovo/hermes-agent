from hermes_cli import plugins


def test_agent_accepts_and_copies_hook_overrides(monkeypatch):
    import agent.agent_init as agent_init
    from run_agent import AIAgent

    captured = {}

    def fake_init(agent, **kwargs):
        captured.update(kwargs)
        agent.hook_overrides = {
            name: list(callbacks)
            for name, callbacks in (kwargs.get("hook_overrides") or {}).items()
        }

    monkeypatch.setattr(agent_init, "init_agent", fake_init)
    callback = lambda **kwargs: "ok"
    overrides = {"on_session_start": [callback]}

    agent = AIAgent(hook_overrides=overrides)

    assert captured["hook_overrides"] == overrides
    assert agent.hook_overrides == overrides
    assert agent.hook_overrides is not overrides
    assert agent.hook_overrides["on_session_start"] is not overrides["on_session_start"]


def test_agent_turn_restores_previous_hook_scope(monkeypatch):
    import agent.conversation_loop as conversation_loop
    import run_agent
    from run_agent import AIAgent

    callback = lambda **kwargs: "instance"
    agent = object.__new__(AIAgent)
    agent.hook_overrides = {"test_event": [callback]}

    def fake_run(*args, **kwargs):
        assert plugins.invoke_hook("test_event") == ["instance"]
        raise RuntimeError("turn failed")

    monkeypatch.setattr(conversation_loop, "run_conversation", fake_run)
    monkeypatch.setattr(run_agent, "scoped_hook_overrides", plugins.scoped_hook_overrides)

    try:
        agent.run_conversation("hello")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the fake turn to fail")

    assert plugins.has_hook("test_event") is False
