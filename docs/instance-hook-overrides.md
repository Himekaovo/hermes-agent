# Instance Hook Overrides

Hermes keeps the existing process-wide plugin hooks for compatibility and can
also attach callbacks to one `AIAgent` instance.

## Agent construction

Pass a mapping from hook names to callbacks:

```python
agent = AIAgent(
    hook_overrides={
        "on_session_start": [on_start],
        "pre_tool_call": [guard_tool],
    },
)
```

The mapping is copied when the Agent is initialized. Callbacks run after the
global plugin callbacks for the same event and are active only during that
Agent's conversation turn. Exceptions remain isolated by the normal Hook
dispatcher.

## Cron and Subagents

A Cron job may carry a `hooks` mapping using the same shell-hook format as the
global configuration. Those callbacks are built for that job and are not
registered globally. Delegated children receive a copy of the parent's
instance mapping, so modifying one Agent does not affect its siblings or
parent.

Existing global `hooks:` configuration, consent prompts, allowlisting,
timeouts, and `shell=False` execution remain unchanged.
