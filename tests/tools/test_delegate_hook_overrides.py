def test_clone_hook_overrides_copies_callback_lists():
    from tools.delegate_tool import _clone_hook_overrides

    first = lambda **kwargs: "first"
    overrides = {"on_session_start": [first]}

    cloned = _clone_hook_overrides(overrides)

    assert cloned == overrides
    assert cloned is not overrides
    assert cloned["on_session_start"] is not overrides["on_session_start"]
