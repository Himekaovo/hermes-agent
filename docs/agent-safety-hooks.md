# Agent Safety Hooks

Hermes ships a deterministic built-in safety hook set across three lifecycle stages:

1. `pre_llm_call`
2. `post_llm_call`
3. `on_session_end`

The hooks are instance-scoped, profile-scoped, and local-only. They do not call the network, schedule background work, auto-write memory, or mutate user files by themselves.

## Configuration

Profile-scoped config lives under `agent.safety_hooks`:

```yaml
agent:
  safety_hooks:
    enabled: true
    block_high_risk: true
    max_context_chars: 12000
    audit_path: logs/safety/session-archiver.jsonl
```

- `enabled`: disables the built-in safety hooks when `false`.
- `block_high_risk`: when `false`, explicit high-risk `BLOCK` results are downgraded to fail-open `WARN` results.
- `max_context_chars`: caps sanitized payload text used by the hook layer. Positive values are clamped to `12000`.
- `audit_path`: must resolve inside the active `HERMES_HOME` profile. Traversal and absolute external paths are rejected and fall back to the default path with a warning.

Invalid types or invalid ranges never abort startup. Hermes logs a warning and falls back to safe defaults.

## Responsibilities

There are 11 built-in responsibilities:

1. `identity`
2. `pm-mode`
3. `subagent-checklist`
4. `local-recall`
5. `context-propagation`
6. `security-inspector`
7. `egress-inspector`
8. `verification-gate`
9. `a2a-metadata-processor`
10. `generic-postprocessor`
11. `session-archiver`

## Stage Order

`pre_llm_call` always runs in this order:

1. `identity`
2. `pm-mode`
3. `subagent-checklist`
4. `local-recall`
5. `context-propagation`
6. `security-inspector`

`post_llm_call` always runs in this order:

1. `egress-inspector`
2. `verification-gate`
3. `a2a-metadata-processor`
4. `generic-postprocessor`

`on_session_end` runs only:

1. `session-archiver`

## Trusted Payload Fields

The hook layer trusts only structured execution fields, not prompt text, for execution identity and scope:

```json
{
  "agent_id": "...",
  "execution_kind": "interactive | cron | subagent",
  "parent_session_id": "..." ,
  "session_id": "...",
  "task_id": "...",
  "turn_id": "..."
}
```

Required fields are `agent_id`, `execution_kind`, `session_id`, `task_id`, and `turn_id`. Root executions normalize `parent_session_id` to `null`.

## Result Semantics

Every hook returns structured JSON-serializable results:

- `ALLOW`: check passed.
- `WARN`: non-blocking risk or a fail-open downgrade.
- `BLOCK`: explicit violation. This is fail-closed unless `block_high_risk: false` downgrades high-risk blocks to warnings.
- `ERROR`: checker failure, malformed payload, or incomplete execution context.
- `SKIP`: hook intentionally not applicable.

`BLOCK` and `ERROR` are different signals. A checker exception does not become a block, and an explicit block is not silently treated as an error.

## Fail-Open Behavior

- `pre_llm_call`: explicit `BLOCK` stops the model call. `ERROR` is recorded and execution continues.
- `post_llm_call`: explicit `BLOCK` suppresses the unsafe visible response. `ERROR` is recorded and execution continues with the existing response path.
- `on_session_end`: archival never blocks cleanup. Write failures degrade to warnings.

If `block_high_risk` is disabled, high-risk built-in block results are downgraded to `WARN` and marked `fail_open` in metadata.

## Memory Governance Boundary

The hook layer is not a memory writer.

- `pre_llm_call`: recall is read-only.
- `post_llm_call`: hooks may produce audit output and memory candidates only.
- `on_session_end`: `session-archiver` may emit a governance candidate, but Memory Governance remains the only authority that can approve durable memory writes.

Hooks do not write `MEMORY.md`, user profile notes, or Skill files directly.

## Isolation Rules

- Profile isolation: audit files resolve against the active profile `HERMES_HOME`.
- Cron isolation: cron runs do not inherit interactive session context.
- Subagent isolation: subagents inherit only explicitly copied callbacks and explicit structured context.
- No mutable global cross-agent state is required for built-in hook decisions.

## Observability

`session-archiver` writes sanitized JSONL records under the configured `audit_path`. The archive records session identity, task identity, verification status, incident count, and a bounded conversation preview.

The archive path is profile-local and remains inside the active profile root even when multiple profiles, cron jobs, or subagents run in the same process.

## Non-Goals

The built-in safety hooks do not provide:

- network scanning
- scheduler integration
- automatic code edits
- automatic memory persistence
- external moderation services
