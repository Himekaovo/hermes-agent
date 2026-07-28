# Task 2 Report

Task 2 adds the explicit relation type set and insert-result reporting, the
approved lifecycle transition table with required actors, unresolved relation
diagnostics, and advisory evaluation observations that do not mutate status.

Verification:

```text
.venv/bin/pytest tests/tools/test_skillwiki.py -q
83 passed in 0.33s
.venv/bin/python -m compileall -q tools/skillwiki.py
git diff --check
```

Files changed:

- `tools/skillwiki.py`
- `tests/tools/test_skillwiki.py`

Review fix: scoped provenance checks now resolve relation endpoints against the
complete database skill set, so a known target is not reported as unresolved.

```text
.venv/bin/pytest tests/tools/test_skillwiki.py -q
84 passed in 0.33s
.venv/bin/python -m compileall -q tools/skillwiki.py
git diff --check
```

## 2026-07-28 Task 2 Safety Hook Follow-up

- Hardened `agent.safety_hooks.run_safety_checks()` so malformed non-mapping payloads like `None` and raw strings return one JSON-serializable structured `error` result with `reason_code="malformed_payload"` instead of crashing.
- Preserved the existing mapping-path behavior: six-hook order for valid/mapping payloads, explicit `identity_missing` blocking for missing `session_id`, and later-checker exception preservation.

Verification:

```text
./.venv/bin/pytest tests/agent/test_safety_hooks.py -q
...................                                                      [100%]
19 passed in 0.16s
```

## 2026-07-28 Task 2 Safety Hook Lifecycle

- Composed `build_safety_hook_overrides()` into every agent instance before explicit `hook_overrides`, preserving defensive-copy semantics and keeping explicit per-agent callbacks ordered after the built-ins.
- Wired `pre_llm_call` trusted execution context with `agent_id`, `execution_kind`, `session_id`, `task_id`, `turn_id`, and `parent_session_id` where present, so instance-scoped callbacks invoked through `hermes_cli.plugins.invoke_hook()` see the same lifecycle-bound data as global hooks.
- Split pre-LLM hook consumption in `agent.turn_context`: `action="block"` now short-circuits before model transport and returns the normal finalized turn result plus `safety_blocked` and `safety_results`; only non-blocking context flows through the existing bounded/spill path, while ordinary hook errors still fail open.

Verification:

```text
./.venv/bin/pytest tests/agent/test_safety_hooks.py tests/run_agent/test_safety_hook_lifecycle.py tests/test_instance_hook_overrides.py -q
27 passed in 4.90s

./.venv/bin/pytest tests/agent/test_api_content_sidecar.py tests/agent/test_gateway_turn_sidecar.py -q
41 passed in 20.38s
```
