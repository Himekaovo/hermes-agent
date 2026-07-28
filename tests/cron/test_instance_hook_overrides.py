from hermes_cli import plugins


def test_cron_job_hook_overrides_are_built_from_job_config(monkeypatch):
    import cron.scheduler as scheduler

    explicit = lambda **kwargs: "job"
    expected = {"pre_llm_call": [explicit]}
    seen = {}

    def fake_build(cfg, *, accept_hooks=False):
        seen["cfg"] = cfg
        seen["accept_hooks"] = accept_hooks
        return expected

    monkeypatch.setattr(scheduler, "build_instance_hooks", fake_build)

    result = scheduler._resolve_cron_hook_overrides(
        {"hooks": {"pre_llm_call": [{"command": "python job.py"}]}},
        {"hooks_auto_accept": True},
    )

    assert result["pre_llm_call"][-1] is explicit
    assert len(result["pre_llm_call"]) == 2
    assert "post_llm_call" in result
    assert "on_session_end" in result
    assert seen["cfg"]["hooks"]
    assert seen["cfg"]["hooks_auto_accept"] is True
    assert seen["accept_hooks"] is True


def test_cron_job_hook_overrides_include_fresh_safety_callbacks(monkeypatch):
    import cron.scheduler as scheduler

    explicit = lambda **kwargs: "job"
    expected = {"pre_llm_call": [explicit]}

    monkeypatch.setattr(
        scheduler,
        "build_instance_hooks",
        lambda cfg, *, accept_hooks=False: expected,
    )

    result = scheduler._resolve_cron_hook_overrides(
        {"hooks": {"pre_llm_call": [{"command": "python job.py"}]}},
        {"hooks_auto_accept": False},
    )

    assert result["pre_llm_call"] is not expected["pre_llm_call"]
    assert result["pre_llm_call"][-1] is explicit
    assert len(result["pre_llm_call"]) == 2
    assert "post_llm_call" in result
    assert "on_session_end" in result


def test_cron_fresh_context_does_not_inherit_interactive_hook_scope():
    import cron.scheduler as scheduler

    def interactive_only(**kwargs):
        return "interactive"

    with plugins.scoped_hook_overrides({"test_event": [interactive_only]}):
        assert plugins.invoke_hook("test_event") == ["interactive"]
        assert scheduler._fresh_cron_context().run(plugins.invoke_hook, "test_event") == []


def test_cron_without_job_hooks_has_no_instance_overrides():
    import cron.scheduler as scheduler

    result = scheduler._resolve_cron_hook_overrides({}, {})

    assert "pre_llm_call" in result
    assert "post_llm_call" in result
    assert "on_session_end" in result
