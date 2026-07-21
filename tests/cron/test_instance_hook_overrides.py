def test_cron_job_hook_overrides_are_built_from_job_config(monkeypatch):
    import cron.scheduler as scheduler

    expected = {"pre_llm_call": [lambda **kwargs: "job"]}
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

    assert result is expected
    assert seen["cfg"]["hooks"]
    assert seen["cfg"]["hooks_auto_accept"] is True


def test_cron_without_job_hooks_has_no_instance_overrides():
    import cron.scheduler as scheduler

    assert scheduler._resolve_cron_hook_overrides({}, {}) == {}
