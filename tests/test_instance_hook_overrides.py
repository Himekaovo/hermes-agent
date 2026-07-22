from hermes_cli import plugins
from hermes_cli.plugins import OBSERVER_SCHEMA_VERSION


def test_invoke_hook_combines_global_and_active_instance_callbacks(monkeypatch):
    manager = plugins.PluginManager()
    monkeypatch.setattr(plugins, "_plugin_manager", manager)

    calls = []

    def global_hook(**kwargs):
        calls.append(("global", kwargs["value"]))
        return "global-result"

    def instance_hook(**kwargs):
        calls.append(("instance", kwargs["value"]))
        return "instance-result"

    manager._hooks["test_event"] = [global_hook]

    with plugins.scoped_hook_overrides({"test_event": [instance_hook]}):
        assert plugins.has_hook("test_event") is True
        assert plugins.invoke_hook("test_event", value=7) == [
            "global-result",
            "instance-result",
        ]

    assert calls == [("global", 7), ("instance", 7)]


def test_instance_hook_scope_is_reset_after_exit(monkeypatch):
    manager = plugins.PluginManager()
    monkeypatch.setattr(plugins, "_plugin_manager", manager)

    def instance_hook(**kwargs):
        return "instance-result"

    assert plugins.has_hook("test_event") is False
    with plugins.scoped_hook_overrides({"test_event": [instance_hook]}):
        assert plugins.has_hook("test_event") is True
    assert plugins.has_hook("test_event") is False


def test_instance_hook_callbacks_are_isolated_from_input_mapping(monkeypatch):
    manager = plugins.PluginManager()
    monkeypatch.setattr(plugins, "_plugin_manager", manager)

    def first(**kwargs):
        return "first"

    def second(**kwargs):
        return "second"

    callbacks = {"test_event": [first]}
    with plugins.scoped_hook_overrides(callbacks):
        callbacks["test_event"].append(second)
        assert plugins.invoke_hook("test_event") == ["first"]


def test_global_and_instance_hooks_receive_same_schema_version(monkeypatch):
    manager = plugins.PluginManager()
    monkeypatch.setattr(plugins, "_plugin_manager", manager)
    seen = []

    def global_hook(**kwargs):
        seen.append(("global", kwargs["telemetry_schema_version"]))

    def instance_hook(**kwargs):
        seen.append(("instance", kwargs["telemetry_schema_version"]))

    manager._hooks["test_event"] = [global_hook]
    with plugins.scoped_hook_overrides({"test_event": [instance_hook]}):
        plugins.invoke_hook("test_event")

    assert seen == [
        ("global", OBSERVER_SCHEMA_VERSION),
        ("instance", OBSERVER_SCHEMA_VERSION),
    ]


def test_explicit_schema_version_reaches_global_and_instance_hooks(monkeypatch):
    manager = plugins.PluginManager()
    monkeypatch.setattr(plugins, "_plugin_manager", manager)
    seen = []

    def global_hook(**kwargs):
        seen.append(kwargs["telemetry_schema_version"])

    def instance_hook(**kwargs):
        seen.append(kwargs["telemetry_schema_version"])

    manager._hooks["test_event"] = [global_hook]
    with plugins.scoped_hook_overrides({"test_event": [instance_hook]}):
        plugins.invoke_hook("test_event", telemetry_schema_version="custom")

    assert seen == ["custom", "custom"]
