# Instance Hook Override Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each Hermes Agent instance isolated Hook overrides while keeping the existing global plugin Hook registry compatible.

**Architecture:** Add a ContextVar-based active override layer to `hermes_cli.plugins`; bind it around `AIAgent.run_conversation()`. Build per-instance shell callbacks without registering them globally, then pass them through Cron and delegate construction.

**Tech Stack:** Python stdlib, `contextvars`, existing pytest suite, existing shell-hook parser and PluginManager.

## Global Constraints

- Preserve the existing global `hooks:` configuration and `invoke_hook()` contract.
- Do not add runtime dependencies.
- Keep shell commands `shell=False` and preserve allowlist/consent behavior.
- Instance hook failures must not prevent Agent, Cron, or subagent execution.
- Child override mappings must be copied so instances remain isolated.

---

### Task 1: Add active instance-hook resolution

**Files:**
- Modify: `hermes_cli/plugins.py`
- Test: `tests/test_instance_hook_overrides.py`

**Interfaces:**
- Produce `scoped_hook_overrides(overrides)` context manager.
- Extend module-level `invoke_hook()` and `has_hook()` to include active instance callbacks.

- [ ] Write tests for global plus instance callback ordering, `has_hook`, context reset, and copied list isolation.
- [ ] Run `pytest tests/test_instance_hook_overrides.py -q` and confirm the new tests fail.
- [ ] Implement the ContextVar and callback resolution with per-callback exception isolation.
- [ ] Run the focused test and the existing plugin hook tests.
- [ ] Commit `feat: add instance hook override context`.

### Task 2: Add per-instance shell-hook builder

**Files:**
- Modify: `agent/shell_hooks.py`
- Test: `tests/agent/test_shell_hooks.py` or the existing shell-hook test module

**Interfaces:**
- Produce `build_instance_hooks(cfg, accept_hooks=False) -> Dict[str, List[Callable]]`.
- Reuse `_parse_hooks_block`, `_prompt_and_record`, and `_make_callback` without modifying `PluginManager._hooks`.

- [ ] Add tests proving the builder returns callbacks and leaves global registration unchanged.
- [ ] Run the focused tests and confirm failure.
- [ ] Implement the builder with safe-mode, allowlist, and consent behavior matching global registration.
- [ ] Run all shell-hook tests.
- [ ] Commit `feat: build isolated shell hook overrides`.

### Task 3: Bind overrides to AIAgent turns

**Files:**
- Modify: `run_agent.py`
- Modify: `agent/agent_init.py`
- Test: `tests/run_agent/test_instance_hook_overrides.py`

**Interfaces:**
- Add optional `hook_overrides` to `AIAgent` and `init_agent`.
- Store a defensive copy on `agent.hook_overrides`.
- Bind `scoped_hook_overrides(agent.hook_overrides)` around the existing conversation call.

- [ ] Add tests for constructor storage and reset after a successful and failing turn.
- [ ] Run focused tests and confirm failure.
- [ ] Implement the parameter forwarding, defensive copy, and context manager scope.
- [ ] Run focused Agent tests plus existing run-agent hook tests.
- [ ] Commit `feat: bind hook overrides to agent turns`.

### Task 4: Inject Cron and delegate overrides

**Files:**
- Modify: `cron/scheduler.py`
- Modify: `tools/delegate_tool.py`
- Test: `tests/cron/test_instance_hook_overrides.py`
- Test: `tests/tools/test_delegate_tool.py` or the existing delegate test module

**Interfaces:**
- Cron job `hooks` config is converted through `build_instance_hooks` and passed as `hook_overrides`.
- `_build_child_agent()` passes a copied parent mapping as `hook_overrides`.

- [ ] Add tests for job-scoped hooks and child-copy isolation.
- [ ] Run focused tests and confirm failure.
- [ ] Implement guarded builders and constructor forwarding.
- [ ] Run Cron/delegate focused tests.
- [ ] Commit `feat: scope hooks to cron and subagents`.

### Task 5: Full verification and documentation

**Files:**
- Modify: `docs/skillwiki.md` only if hook behavior needs cross-reference; otherwise no production docs change
- Test: existing hook, run-agent, Cron, and delegate suites

- [ ] Run the focused Hook/Cron/delegate suite.
- [ ] Run the relevant full regression suites.
- [ ] Run `python -m compileall` on modified Python modules and `git diff --check`.
- [ ] Review the final diff for global behavior changes and mutable-state leaks.
- [ ] Commit any final test-only or doc clarification changes.
- [ ] Push `feature/my-hermes-agent` and report the commit and verification output.
