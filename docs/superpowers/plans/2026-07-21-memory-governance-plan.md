# Memory Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已批准的方案 1 落到 MyHermes 的本地内置记忆写入链路，提供可回滚备份、保护区、确定性写入闸门、Step Buffer 和 Meta Skill 建议。

**Architecture:** 新建纯标准库 `tools/memory_governance.py`，集中处理快照、保护区解析、质量/冲突检测、Step Buffer 和 Meta Skill。`MemoryStore` 在现有参数校验、写入审批和文件锁之后调用治理层；journey 编辑/删除与 `hermes memory` CLI 复用同一模块。

**Tech Stack:** Python 3, stdlib (`json`, `hashlib`, `datetime`, `pathlib`, `re`), pytest, argparse。

## Global Constraints

- 只治理当前 profile 下的 `MEMORY.md`、`USER.md` 和 `memories/governance/`、`memories/l2/versions/`。
- 不增加第三方依赖，不联网，不引入后台调度、模型自改写、Auto-Evolve、自动合并、自动删除或自动回滚。
- 保留现有 `write_approval`、prompt/exfiltration scan、字符预算、文件锁和原子写入语义。
- 治理拒绝必须返回 JSON 可序列化的 `success: false` 与稳定 `gate` 原因码。
- 每个行为先写失败测试并运行确认失败，再写最小生产代码。

---

### Task 1: Build the governance policy module

**Files:**
- Create: `tools/memory_governance.py`
- Create: `tests/tools/test_memory_governance.py`

**Interfaces:**
- `snapshot(path: Path, versions_dir: Path, *, target: str, reason: str, operation: str) -> dict[str, Any]`
- `list_snapshots(versions_dir: Path, *, target: str | None = None, limit: int = 20) -> list[dict[str, Any]]`
- `rollback(snapshot_id: str, *, target_path: Path, versions_dir: Path, target: str) -> dict[str, Any]`
- `protected_entries(entries: list[str]) -> list[dict[str, Any]]`
- `preflight(*, target: str, current_entries: list[str], proposed_entries: list[str], operation: str, governance_dir: Path) -> dict[str, Any]`
- `record_step(governance_dir: Path, pattern: str, note: str | None = None) -> dict[str, Any]`
- `list_steps(governance_dir: Path, limit: int = 20) -> list[dict[str, Any]]`
- `record_meta(governance_dir: Path, strategy: str, result: str, note: str | None = None) -> dict[str, Any]`
- `list_meta(governance_dir: Path, limit: int = 20) -> list[dict[str, Any]]`

- [ ] **Step 1: Write failing tests for snapshots and protected markers**

  Cover exact pre-write bytes, JSON sidecar fields, newest-first listing, rollback backup-before-restore, `<!-- SLOW_UPDATE -->`, and named protected spans.

- [ ] **Step 2: Run the focused tests to verify the missing module fails**

  Run: `pytest tests/tools/test_memory_governance.py -q`

  Expected: collection/import failure because `tools.memory_governance` does not exist.

- [ ] **Step 3: Implement snapshots, rollback, and protected-region parsing**

  Use UTC timestamp plus SHA-256 prefix in snapshot ids, write sidecars atomically, ignore malformed sidecars when listing, and use the existing `MemoryStore._write_file` only from integration code.

- [ ] **Step 4: Write failing tests for gate, Step Buffer, and Meta Skill**

  Cover delimiter abuse, low-quality content, Jaccard near duplicates at `0.82`, contradictory boolean preference toggles, protected replace/remove, Step Buffer blocking at count `2`, and advisory Meta Skill recommendations.

- [ ] **Step 5: Run the tests and verify these behaviors fail for the expected missing APIs**

  Run: `pytest tests/tools/test_memory_governance.py -q`

  Expected: failures naming the unimplemented gate and record/list functions.

- [ ] **Step 6: Implement deterministic preflight and JSONL records**

  Normalize tokens with stdlib regex, return `quality_score`, `conflicts`, `protected`, `step_buffer`, and `recommendations`, preserve malformed JSONL lines on read, and create governance directories lazily.

- [ ] **Step 7: Run the governance tests and refactor only while green**

  Run: `pytest tests/tools/test_memory_governance.py -q`

  Expected: all tests in the new module pass.

- [ ] **Step 8: Commit the independent policy module**

  Run:
  ```bash
  git add tools/memory_governance.py tests/tools/test_memory_governance.py
  git commit -m "feat: add local memory governance policies"
  ```

### Task 2: Gate MemoryStore mutations

**Files:**
- Modify: `tools/memory_tool.py` at `MemoryStore.add`, `replace`, `remove`, `apply_batch`, and shared write helpers
- Modify: `tests/tools/test_memory_tool.py`

**Interfaces:**
- `MemoryStore` calls governance after reloading current entries and before `_write_file`.
- Successful mutation responses may include `governance` metadata; rejected responses retain `success: false` and add a stable `gate`.

- [ ] **Step 1: Add failing MemoryStore integration tests**

  Assert accepted add creates a snapshot, protected replace/remove are rejected without mutation, batch rejection is atomic, and `apply_memory_pending()` still invokes governance.

- [ ] **Step 2: Run the focused integration tests to verify they fail**

  Run: `pytest tests/tools/test_memory_tool.py -q`

  Expected: new assertions fail because mutations currently write without governance snapshots or protection checks.

- [ ] **Step 3: Add profile-scoped governance path helpers and preflight calls**

  Re-read entries under the existing `RLock`, build proposed entries for each operation, reject protected changes and policy failures before disk mutation, snapshot existing files before accepted writes, and keep batch writes all-or-nothing.

- [ ] **Step 4: Run the focused tests and the pending-write tests**

  Run: `pytest tests/tools/test_memory_tool.py tests/tools/test_write_approval.py -q`

  Expected: all selected tests pass.

- [ ] **Step 5: Commit the MemoryStore integration**

  Run:
  ```bash
  git add tools/memory_tool.py tests/tools/test_memory_tool.py
  git commit -m "feat: govern built-in memory mutations"
  ```

### Task 3: Govern journey memory edits and deletes

**Files:**
- Modify: `agent/learning_mutations.py` at `_edit_memory`, `_delete_memory`, and `_write_memory`
- Modify: `tests/agent/test_learning_mutations.py`

- [ ] **Step 1: Add failing tests for journey snapshots and protected deletion**

  Use a temporary `HERMES_HOME`, edit/delete a memory through the public mutation functions, assert a version snapshot exists, and assert a `SLOW_UPDATE` memory cannot be deleted.

- [ ] **Step 2: Run the focused tests to verify the bypass is exposed**

  Run: `pytest tests/agent/test_learning_mutations.py -q`

  Expected: snapshot/protection assertions fail because journey writes currently call `_write_file` directly.

- [ ] **Step 3: Route journey mutation checks through governance**

  Preserve the existing node-id resolution and atomic write behavior, snapshot before accepted edit/delete, reject protected entries, and leave duplicate/quality checks advisory for this explicit user action.

- [ ] **Step 4: Run the journey tests**

  Run: `pytest tests/agent/test_learning_mutations.py -q`

  Expected: all selected tests pass.

- [ ] **Step 5: Commit the journey integration**

  Run:
  ```bash
  git add agent/learning_mutations.py tests/agent/test_learning_mutations.py
  git commit -m "feat: protect journey memory edits"
  ```

### Task 4: Add governance CLI commands

**Files:**
- Modify: `hermes_cli/subcommands/memory.py`
- Modify: `hermes_cli/main.py`
- Create: `tests/hermes_cli/test_memory_governance_cli.py`

**Interfaces:**
- `hermes memory versions list [--target memory|user] [--limit N]`
- `hermes memory versions rollback <version_id> --target memory|user --yes`
- `hermes memory step record <pattern> [--note <text>]`
- `hermes memory step list [--limit N]`
- `hermes memory meta log <strategy> --result success|failure [--note <text>]`
- `hermes memory meta list [--limit N]`

- [ ] **Step 1: Add failing parser and command tests**

  Assert all six command families parse, records are profile-scoped, confirmed rollback restores content, and missing `--yes` refuses to mutate.

- [ ] **Step 2: Run the CLI tests to verify missing commands fail**

  Run: `pytest tests/hermes_cli/test_memory_governance_cli.py -q`

  Expected: parser/dispatch failures because only provider setup/status/off/reset exist.

- [ ] **Step 3: Extend the parser and dispatch to governance helpers**

  Keep existing provider command behavior untouched, print human-readable JSON/table output, resolve the active profile through `get_memory_dir()`, and require `--yes` for rollback.

- [ ] **Step 4: Run CLI and existing memory reset tests**

  Run: `pytest tests/hermes_cli/test_memory_governance_cli.py tests/hermes_cli/test_memory_reset.py -q`

  Expected: all selected tests pass.

- [ ] **Step 5: Commit the CLI surface**

  Run:
  ```bash
  git add hermes_cli/subcommands/memory.py hermes_cli/main.py tests/hermes_cli/test_memory_governance_cli.py
  git commit -m "feat: expose memory governance commands"
  ```

### Task 5: Full verification and handoff

**Files:**
- Modify: `docs/superpowers/specs/2026-07-21-memory-governance-design.md` only if implementation clarifies a contract
- Modify: `README.md` or the existing memory docs only if the CLI needs user-facing documentation

- [ ] **Step 1: Run the complete focused regression suite**

  Run:
  ```bash
  pytest tests/tools/test_memory_governance.py tests/tools/test_memory_tool.py tests/agent/test_learning_mutations.py tests/hermes_cli/test_memory_governance_cli.py -q
  ```

  Expected: exit code `0` and zero failures.

- [ ] **Step 2: Run repository formatting and diff checks**

  Run: `git diff --check`

  Expected: no output and exit code `0`.

- [ ] **Step 3: Inspect the final diff for scope and unsafe behavior**

  Run: `git diff --stat HEAD~4..HEAD && git diff --name-only HEAD~4..HEAD`

  Confirm only the governance module, memory integrations, CLI, focused tests, and necessary docs are present; confirm no scheduler/network/Auto-Evolve code was added.

- [ ] **Step 4: Commit any final documentation-only correction**

  Run:
  ```bash
  git add docs README.md
  git commit -m "docs: document memory governance workflow"
  ```

  Skip this step when no documentation correction is needed.
