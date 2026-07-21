# Subagent Delegation Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Hermes 子 Agent 委派增强为可分类、有限重试、可兜底且可审计的执行链路。

**Architecture:** 在现有 `tools/delegate_tool.py` 中增加纯函数式失败分类、重试配置读取和结果包装；保留现有 `_run_single_child` 负责一次执行，并由 `delegate_task` 的单任务/批量执行路径负责重试与聚合。子 Agent 每次重试重新构建，避免复用异常会话。现有 TUI 事件、摘要预算和成本统计继续复用。

**Tech Stack:** Python 3, unittest/pytest, existing Hermes delegation runtime.

## Global Constraints

- 不改变 `delegate_task` 的模型可见入参 schema。
- 默认 `max_retries=2`，`retry_backoff_seconds=1.0`；硬失败和中断不重试。
- 失败结果必须明确标注，不得把兜底说明伪装成正常成功。
- 不加入定时调度、联网搜索、Smart Routing、Always-on Executor 或 Auto-Evolve。

---

### Task 1: Add deterministic failure classification and retry configuration

**Files:**
- Modify: `tools/delegate_tool.py` near the existing delegation configuration helpers.
- Test: `tests/tools/test_delegate.py`

**Interfaces:**
- Produce `_classify_delegate_failure(entry_or_error) -> tuple[str, str]`.
- Produce `_get_max_retries() -> int` and `_get_retry_backoff_seconds() -> float`.

- [ ] **Step 1: Write failing tests**

Add tests for timeout/network/empty response as `soft`, max iterations/interrupted/tool unavailable as `hard`, and config values including invalid/negative inputs.

- [ ] **Step 2: Run focused tests and verify failure**

Run `python -m pytest tests/tools/test_delegate.py -k 'failure_class or retry' -q`.
Expected: collection succeeds and new tests fail because the helpers do not exist.

- [ ] **Step 3: Implement the helpers**

Use stable reason codes such as `timeout`, `network_error`, `empty_response`, `temporary_tool_error`, `max_iterations`, `interrupted`, `tool_unavailable`, and `unknown_error`. Read values from `delegation.max_retries` and `delegation.retry_backoff_seconds`, clamp retries at zero and backoff at zero, and fall back to defaults for invalid values.

- [ ] **Step 4: Run focused tests and verify pass**

Run `python -m pytest tests/tools/test_delegate.py -k 'failure_class or retry' -q`.
Expected: all selected tests pass.

- [ ] **Step 5: Commit**

Run `git add tools/delegate_tool.py tests/tools/test_delegate.py && git commit -m "feat: classify subagent delegation failures"`.

### Task 2: Add retry wrapper and structured fallback results

**Files:**
- Modify: `tools/delegate_tool.py` in the existing single-task and batch execution paths.
- Test: `tests/tools/test_delegate.py`

**Interfaces:**
- Preserve `_run_single_child(...) -> dict` as one attempt.
- Add an internal retry wrapper that accepts a child factory, goal, parent agent, and task index, and returns the existing result fields plus `attempts`, `attempt_history`, `failure_class`, and `fallback` when applicable.

- [ ] **Step 1: Write failing tests**

Use fake child factories/results to test soft-failure-then-success, soft-failure exhaustion, hard-failure no-retry, and the presence of `fallback.next_action` and per-attempt history.

- [ ] **Step 2: Run focused tests and verify failure**

Run `python -m pytest tests/tools/test_delegate.py -k 'retry_attempt or fallback or attempt_history' -q`.
Expected: new tests fail before the retry wrapper exists.

- [ ] **Step 3: Implement the wrapper**

Execute the first attempt, classify its result, retry only soft failures while attempts remain, sleep using bounded exponential backoff, and return a final structured result. Keep successful legacy status as `completed`; use `final_failed` only after recovery is exhausted and `fallback_succeeded` only when a non-empty partial result is explicitly available.

- [ ] **Step 4: Wire the wrapper into delegate execution**

Update both single and batch paths to construct a fresh child for each retry while preserving the existing concurrency executor, parent activity updates, summary budget, and event delivery. Ensure retry attempts do not create duplicate top-level result entries.

- [ ] **Step 5: Run focused tests and verify pass**

Run `python -m pytest tests/tools/test_delegate.py -k 'retry or fallback or attempt_history' -q`.
Expected: all selected tests pass.

- [ ] **Step 6: Commit**

Run `git add tools/delegate_tool.py tests/tools/test_delegate.py && git commit -m "feat: recover failed subagent delegations"`.

### Task 3: Document configuration and run regression verification

**Files:**
- Modify: `AGENTS.md` delegation section.
- Modify: `website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-hermes-agent.md` if the user-facing delegation reference mirrors the config table.
- Test: existing `tests/tools/test_delegate.py` and timeout diagnostic tests.

- [ ] **Step 1: Document retry semantics**

Add the two config keys, defaults, soft/hard failure behavior, and structured final failure semantics to the existing delegation documentation.

- [ ] **Step 2: Run focused regression suite**

Run `python -m pytest tests/tools/test_delegate.py tests/tools/test_delegate_subagent_timeout_diagnostic.py -q`.
Expected: all tests pass.

- [ ] **Step 3: Run diff hygiene checks**

Run `git diff --check` and inspect `git diff --stat`.
Expected: exit code 0 and only delegation implementation, tests, docs, plan, and spec files changed.

- [ ] **Step 4: Commit documentation and verification-ready changes**

Run `git add AGENTS.md website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-hermes-agent.md && git commit -m "docs: describe subagent recovery policy"`.
