# Memory Governance Design

## Goal

把《给 Agent 加了“刹车+导航”》里的五个机制落到 MyHermes 的内置本地记忆写入链路：`MEMORY.md` 和 `USER.md` 写入前可拦截，写入前可备份，写坏后可回滚，重复失败模式会被阻断，有效策略会被记录并在下次写入时作为建议返回。

## Scope

本次采用方案 1：新增独立的 memory governance 层，然后接入现有 `tools/memory_tool.py` 与用户手动的 journey memory edit/delete 路径。首期只治理 profile-scoped 的本地内置记忆文件：

- `memories/MEMORY.md`
- `memories/USER.md`
- `memories/l2/versions/`
- `memories/governance/step_buffer.jsonl`
- `memories/governance/meta_skill.jsonl`

不会接入外部 memory providers、holographic provider、联网检索、定时任务、模型驱动自改写、Auto-Evolve、自动合并、自动删除或自动调度。

## Current Context

现有 `MemoryStore` 已经提供：

- profile-scoped memory directory resolution via `get_memory_dir()`;
- `MEMORY.md` / `USER.md` entry parsing with the `§` delimiter;
- file locks and atomic writes;
- prompt-injection and exfiltration scanning;
- character budgets and consolidation errors;
- existing write approval staging through `tools.write_approval`.

Governance should reuse these guarantees instead of replacing them. It should sit after existing parameter validation and write approval, but before the final file mutation. Staged writes replayed through `apply_memory_pending()` must still pass governance because the store methods enforce the governance checks.

## Architecture

### 1. Governance module

Create `tools/memory_governance.py` as a deterministic, stdlib-only module. It owns all policy that is not already part of `MemoryStore`:

- version snapshot creation and listing;
- rollback restore helpers;
- protected region parsing;
- quality scoring;
- conflict detection;
- step buffer matching;
- meta strategy logging and recommendation.

The module exposes small functions that can be called from `MemoryStore`, CLI commands, and journey mutations without importing the full agent runtime.

### 2. MemoryStore integration

Modify `MemoryStore.add()`, `replace()`, `remove()`, and `apply_batch()` so they run governance after the current reload and validation steps, but before changing entries on disk.

The write flow becomes:

1. Validate tool parameters and scan new content for prompt/exfil threats.
2. Run existing `write_approval` gate if configured.
3. Re-read current `MEMORY.md` or `USER.md` under the existing file lock.
4. Build the proposed final entry list.
5. Run governance preflight against current entries and proposed entries.
6. If blocked, return `success: false`, `gate: <reason_code>`, and structured details. Do not write.
7. If allowed, create a version snapshot of the current file.
8. Persist the proposed entries with the existing atomic writer.
9. Return the existing success response plus compact governance metadata when useful.

### 3. Manual journey edits

`agent/learning_mutations.py` currently rewrites memory files directly through `MemoryStore._write_file()`. Update its memory edit/delete path to use the same governance module for backups and protected-entry checks. Because this is a user-initiated edit surface, duplicate/quality gates are advisory unless the edit touches a protected region or matches repeated failure patterns.

## Mechanisms

### Slow Update and version backup

Every accepted mutation to `MEMORY.md` or `USER.md` creates a snapshot under:

```text
memories/l2/versions/
```

Snapshot files use stable names:

```text
YYYYMMDDTHHMMSSZ-<target>-<sha8>.md
YYYYMMDDTHHMMSSZ-<target>-<sha8>.json
```

The `.md` file contains the exact pre-write bytes of the target memory file. The `.json` sidecar records:

- `id`
- `target`
- `created_at`
- `source_path`
- `sha256`
- `reason`
- `operation`
- `entry_count`
- `byte_count`

Rollback is manual only. A rollback always snapshots the current file first, then restores the selected `.md` snapshot using the same atomic writer. This prevents a rollback from becoming destructive.

### Protected regions

Governance parses protected entries and regions from `MEMORY.md` / `USER.md`.

Supported markers:

```markdown
<!-- SLOW_UPDATE -->
```

inside an entry marks the whole entry as protected.

```markdown
<!-- PROTECTED_START: name -->
...
<!-- PROTECTED_END: name -->
```

marks a protected span. Because the existing memory tool mutates `§`-delimited entries, any entry intersecting a protected span is treated as protected.

Rules:

- `replace` and `remove` are blocked when the matched existing entry is protected.
- `batch` is blocked if any operation would modify or remove a protected entry.
- `add` is allowed, including adding a new protected entry, but it still runs quality, conflict, and step-buffer checks.
- Rollback is allowed because it is explicit manual recovery and creates its own backup first.

Blocked responses include:

```json
{
  "success": false,
  "gate": "protected_region",
  "protected": [{"name": "name", "entry_index": 0}]
}
```

### Gate mechanism

The gate is deterministic and conservative. It blocks only cases that are likely to make local memory worse:

- exact delimiter abuse: content containing the raw `\n§\n` separator;
- very low quality content such as empty-after-normalization, one-word scraps, or large raw dumps;
- near-duplicate entries using normalized token overlap / Jaccard similarity;
- obvious contradictory preference toggles when the same subject has opposing boolean values.

The default conflict threshold is high enough to avoid blocking ordinary nearby facts:

```text
similarity >= 0.82
```

When blocked for duplicate or conflict, the response includes:

```json
{
  "success": false,
  "gate": "conflict_detected",
  "quality_score": 0.74,
  "conflicts": [
    {
      "entry_index": 1,
      "similarity": 0.91,
      "preview": "..."
    }
  ]
}
```

Quality score is returned for visibility, but only hard-fail thresholds block. The first version should avoid model calls and avoid trying to infer deep semantic contradictions.

### Step Buffer

The step buffer records repeated failed memory-write patterns in:

```text
memories/governance/step_buffer.jsonl
```

Each line stores:

- `pattern`
- `normalized_pattern`
- `count`
- `first_seen_at`
- `last_seen_at`
- `note`

The CLI supports:

```text
hermes memory step record <pattern> [--note <text>]
hermes memory step list
```

During preflight, governance compares the proposed content and operation summary to recorded patterns. If a similar pattern has `count >= 2`, the write is blocked:

```json
{
  "success": false,
  "gate": "step_buffer",
  "pattern": "...",
  "count": 2
}
```

The agent may record failures manually through the CLI or a later explicit tool surface. The first implementation does not automatically mine failures from logs.

### Meta Skill

The meta skill records strategy outcomes in:

```text
memories/governance/meta_skill.jsonl
```

CLI:

```text
hermes memory meta log <strategy> --result success|failure [--note <text>]
hermes memory meta list
```

Preflight computes lightweight recommendations from strategies with more successes than failures and normalized overlap with the current operation. Recommendations are advisory only and never mutate the requested write:

```json
{
  "recommendations": [
    {
      "strategy": "merge overlapping entries before adding a new rule",
      "successes": 3,
      "failures": 1
    }
  ]
}
```

## CLI

Extend `hermes memory` with local-governance commands while keeping provider setup/status/off/reset behavior unchanged:

```text
hermes memory versions list [--target memory|user] [--limit N]
hermes memory versions rollback <version_id> --target memory|user --yes
hermes memory step record <pattern> [--note <text>]
hermes memory step list [--limit N]
hermes memory meta log <strategy> --result success|failure [--note <text>]
hermes memory meta list [--limit N]
```

Rollback requires `--yes` for non-interactive confirmation. If `--yes` is missing, the command prints the target, version id, snapshot age, and current file path, then asks for `yes`.

## Error Handling

- Governance fails closed for policy violations and fails open only if snapshot creation itself cannot run because the target file does not yet exist.
- If snapshot creation fails for an existing file, the write is rejected with `gate: version_backup_failed`.
- Malformed governance JSONL records are ignored for reads and preserved on disk.
- Missing governance directories are created lazily.
- CLI commands should print human-readable messages, while tool responses stay JSON-serializable.

## Testing

Use TDD. Add tests before implementation:

- `tests/tools/test_memory_governance.py`
  - creates version snapshot with sidecar metadata;
  - lists snapshots newest first;
  - rollback restores a selected snapshot and snapshots current content first;
  - parses `<!-- SLOW_UPDATE -->` and protected start/end markers;
  - blocks protected replace/remove;
  - blocks near-duplicate/conflicting writes with `gate: conflict_detected`;
  - records step patterns and blocks once count reaches 2;
  - logs meta strategy outcomes and returns recommendations.

- `tests/tools/test_memory_tool.py`
  - `add` creates a version snapshot before write;
  - `replace` and `remove` are blocked for protected entries;
  - `apply_batch` is blocked atomically if one operation violates governance;
  - approved pending writes still pass governance through `apply_memory_pending()`.

- `tests/agent/test_learning_mutations.py`
  - journey memory edit/delete creates a version snapshot;
  - protected journey memory delete is refused.

- `tests/hermes_cli/test_memory_governance_cli.py`
  - parser accepts version, rollback, step, and meta subcommands;
  - CLI rollback restores the target file when confirmed;
  - CLI step/meta commands write profile-scoped governance records.

Run focused tests first, then the relevant memory/journey/CLI subset:

```bash
pytest tests/tools/test_memory_governance.py -q
pytest tests/tools/test_memory_tool.py tests/agent/test_learning_mutations.py tests/hermes_cli/test_memory_governance_cli.py -q
git diff --check
```

## Compatibility and Safety

- Existing memory tool schema remains compatible.
- Existing `memory setup/status/off/reset` commands keep their behavior.
- Existing write approval remains a separate permission gate.
- Governance uses active `HERMES_HOME`, so profile isolation is preserved.
- No external network calls, no new runtime dependencies, no background scheduler.
- No automatic deletion, merging, rewriting, or rollback.
- Success responses remain terminal so the model does not repeat the same memory write.

## Open Decisions Resolved

- Use `hermes memory ...` instead of adding a new `recall.py` command because this repo already centralizes memory operations there.
- Use deterministic scoring instead of model verification in v1.
- Treat `<!-- SLOW_UPDATE -->` as entry-level protection because current memory writes operate on `§`-delimited entries.
- Keep meta skill advisory in v1 so it cannot silently rewrite the agent's requested operation.
