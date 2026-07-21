# Instance Hook Override Design

## Goal

Allow each `AIAgent` instance to carry its own hook callbacks while preserving the existing process-wide plugin hooks as a compatibility layer.

## Scope

- Add an optional `hook_overrides` mapping to `AIAgent`.
- Resolve hooks as global plugin callbacks plus the active agent's instance callbacks.
- Keep existing `hermes_cli.plugins.invoke_hook()` and `has_hook()` behavior unchanged when no agent override is active.
- Allow shell hook specs to be built for one agent without registering them globally.
- Pass Cron job hook configuration into the Cron agent only.
- Inherit the parent's resolved instance hooks when creating a delegated child.
- Preserve shell-hook consent, allowlisting, `shell=False`, timeout handling, and fail-open callback isolation.

## Non-goals

- No rewrite of the plugin manager or existing hook contracts.
- No automatic sharing of one agent's mutable hook list with another agent.
- No change to global plugin discovery or existing top-level `hooks:` behavior.
- No new dependency, scheduler, network call, or model decision.

## Design

`hermes_cli.plugins` owns a `ContextVar` containing the active instance override mapping. `AIAgent.run_conversation()` binds its own mapping for the duration of the turn and resets it in `finally`. Existing hook call sites therefore gain instance routing without signature churn, and worker threads inherit it through the existing context propagation helpers.

The global plugin manager remains the first callback source. Instance callbacks are appended for the active hook name, so observability and safety plugins remain active while a Cron or subagent can add its own behavior. `has_hook()` reports either source.

`agent.shell_hooks` exposes a builder that parses and validates a config block, applies the existing consent/allowlist rules, and returns callbacks without mutating the global plugin manager. `register_from_config()` continues to use the global registration path.

Cron reads an optional job-level `hooks` mapping and builds instance callbacks. Delegated children receive a copied mapping of callback lists from their parent; later mutation of one instance cannot affect its parent or siblings.

## Error Handling

Malformed hook configuration is skipped using existing warnings. Callback exceptions remain isolated by the plugin manager. Building instance hooks failing for any reason yields an empty override and does not prevent the Agent, Cron job, or subagent from starting.

## Testing

- Hook resolution combines global and instance callbacks and resets after a turn.
- Agent instances do not share mutable override lists.
- Shell instance registration does not mutate the global manager.
- Cron passes job hooks into its Agent.
- Delegate children inherit a copied override mapping.
- Existing global hook behavior and shell-hook tests remain green.
