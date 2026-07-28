# Mnemosyne Memory Tower Design

## 1. Goals

Implement the first local MyHermes version of Mnemosyne: a four-layer memory
orchestration provider based on the design document "Mnemosyne - General
AI-Agent Memory System Technical Design".

Mnemosyne must organize existing memory capabilities into a single Memory Tower
without becoming a new uncontrolled source of truth. It should make recall
layered, explainable, bounded, and reversible:

- L1 Core: stable identity, principles, preferences, and rules.
- L2 Episodic: structured facts and events with tags, trust, and retrieval
  reasons.
- L3 Skill/API: skill provenance, lifecycle state, relationships, and health.
- L4 Reflection/Experience: local reflection and strategy candidates with decay
  and governance state.

The first implementation uses **A1: Mnemosyne orchestration provider**. It adds a
local `MnemosyneProvider` that composes the existing systems instead of replacing
them.

## 2. Non-goals

This phase does not:

- rewrite the Agent main loop;
- create a parallel full replacement for existing memory providers;
- bypass Memory Governance to mutate `MEMORY.md`, `USER.md`, or L2 facts;
- automatically modify L1 Core memory;
- automatically promote or merge memories across layers;
- delete, rewrite, archive, or roll back memory automatically;
- install, upgrade, or repair skills;
- modify SkillWiki lifecycle states;
- add network calls, remote sync, background provider bridges, or external
  memory backends;
- use model calls for automatic verification, summarization, or evolution.

Automatic promotion, Core rewrites, remote bridge implementations, and any
model-driven memory evolution remain separate future phases requiring explicit
approval and rollback design.

## 3. Existing-system constraints

Mnemosyne must reuse the current MyHermes memory boundaries:

- `agent.memory_provider.MemoryProvider` is the lifecycle interface.
- `agent.memory_manager.MemoryManager` calls provider initialization, system
  prompt blocks, prefetch, sync, queue-prefetch, session-end, compression, and
  memory-write hooks.
- Built-in `tools.memory_tool.MemoryStore` owns profile-scoped `MEMORY.md` and
  `USER.md` parsing, approval staging, file locks, atomic writes, and Memory
  Governance.
- `tools.memory_governance` owns protected regions, snapshots, rollback,
  duplicate/conflict gates, Step Buffer, and Meta Skill recommendations.
- `plugins.memory.holographic` owns structured L2 fact storage, tag routing,
  trust scoring, reconstruct, and read-only retrieval behavior.
- `tools.skillwiki` and `tools.skills_hub` own L3 skill provenance, lifecycle
  state, relations, and health checks.

The active profile's `HERMES_HOME` is the only trusted root for local memory
paths. Mnemosyne must not accept user query text or config values as arbitrary
memory file paths.

## 4. Architecture

Create a bundled local memory provider:

```text
plugins/memory/mnemosyne/
  __init__.py
  plugin.yaml
```

The provider is activated through the existing `memory.provider` config path:

```yaml
memory:
  provider: mnemosyne
```

`MnemosyneProvider` has three internal roles:

- **Observer**: watches turn/session events and creates ephemeral observations
  or L4 candidates.
- **Injector**: performs read-only layered recall and returns bounded context.
- **Refiner**: builds candidate/conflict reports and read-only maintenance
  suggestions.

Provider responsibilities are:

- four-layer capability discovery and orchestration;
- query routing;
- score normalization;
- injection budget control;
- provenance and reason aggregation;
- fail-open behavior when a layer is unavailable;
- generation of observation, candidate, and conflict-report structures.

Provider responsibilities explicitly exclude:

- direct uncontrolled mutation of L1, L2, or L3;
- automatic promotion;
- automatic deletion;
- deciding final long-term memory writes;
- modifying main-loop control flow.

## 5. Four-layer ownership model

### L1 Core

L1 is backed by the existing built-in memory files:

```text
memories/MEMORY.md
memories/USER.md
```

Mnemosyne reads L1 through the current profile-scoped memory store. L1 writes,
edits, deletes, protection, snapshots, and rollback remain owned by Memory
Governance. Mnemosyne may include L1 content in a bounded injection block, but it
must not rewrite L1 or remove protected markers.

File handling rules:

- Missing L1 files fail open.
- Encoding errors or malformed content are logged and skipped for that layer.
- Query text cannot influence the resolved L1 path.
- Full injection versus summary injection is controlled only by trusted config.
- Subagent contexts do not receive full sensitive L1 content by default.

### L2 Episodic

L2 is backed by the existing holographic memory store. Mnemosyne uses its
read-only recall paths for query-sensitive facts and evidence packs.

Required behavior:

- Use reason-bearing search/reconstruct results.
- Use `mark_retrieved=False` for Mnemosyne prefetch and read-only
  reconstruction paths.
- Do not increment retrieval count, trust, or `last_retrieved_at` from
  Mnemosyne aggregation.
- Do not reorder or deduplicate by writing back to the L2 database.
- A L2 failure drops L2 results only; L1, L3, and L4 continue.

L2 fact creation or update remains outside Mnemosyne orchestration. This phase
performs no L2 writes. Any future L4-to-L2 migration must get its own explicit
design and pass the applicable fact-store approval/governance gate before it can
write.

### L3 Skill/API

L3 is backed by SkillWiki and the existing Skills Hub.

Mnemosyne may read:

- visible skill records;
- lifecycle state;
- source provenance;
- relation metadata;
- local drift and health check results;
- advisory evaluation output.

Rules:

- SkillWiki unavailable or corrupt means L3 fails open.
- Only visible and non-disabled skills are considered for injection.
- Incomplete provenance lowers confidence but does not trigger repair.
- Advisory output is not executable instruction.
- Health warnings must not become install, upgrade, or lifecycle actions.
- Mnemosyne never changes SkillWiki lifecycle state.

### L4 Reflection/Experience

L4 is the only new local storage in this phase. It is a reflection and experience
candidate store, not a general fact database.

Store profile-scoped records under:

```text
memories/mnemosyne/l4.jsonl
```

Each valid record uses this contract:

```python
{
    "record_id": "l4_<sha_or_uuid>",
    "kind": "reflection" | "strategy" | "success_pattern" |
            "failure_pattern" | "lesson" | "conflict",
    "content": "...",
    "profile_id": "...",
    "session_id": "...",
    "agent_id": "...",
    "execution_kind": "interactive" | "cron" | "subagent" | "flush",
    "status": "candidate" | "approved" | "rejected" | "archived",
    "confidence": 0.0,
    "decay_score": 1.0,
    "governance_state": "candidate" | "approved" | "rejected" | "archived",
    "source_refs": [{"layer": "L2", "item_id": "fact:123"}],
    "created_at": "2026-07-28T00:00:00Z",
    "last_validated_at": None,
    "archived_at": None
}
```

`on_session_end()` may write L4 candidates through the controlled Mnemosyne L4
candidate API. That is the only new persistence path in this phase. L4 candidate
writes must be profile-scoped, JSON-serializable, bounded, locked, atomic, and
fail-open. They must not imply approval, Core persistence, or L2 fact creation.

Any migration from L4 to L1 or L2 remains a future explicit action and must pass
Memory Governance.

## 6. Data contracts

All layer recall is normalized into immutable `MnemosyneItem` values:

```python
@dataclass(frozen=True)
class MnemosyneItem:
    item_id: str
    layer: Literal["L1", "L2", "L3", "L4"]
    content: str
    reason: str
    score: float
    source: str
    provenance: dict[str, object]
    trust: float | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
```

`reason` is mandatory. It must come from the layer's actual retrieval or
selection signal, not from a later Injector guess.

The provider returns a bounded context block formatted for the existing
`MemoryManager.prefetch_all()` pipeline. The block should include compact layer
labels and reasons, while preserving machine-readable shape in tests.

Example human-facing block:

```text
## Mnemosyne Memory Tower
- [L1 score=1.00] User prefers concise Chinese review summaries.
  reason: core profile entry from USER.md
- [L2 score=0.81] Deployment failure was caused by migration state.
  reason: matched query terms and tag migration
- [L3 score=0.60] skill github:acme/deploy is verified
  reason: SkillWiki lifecycle status and related trigger
- [L4 score=0.42] Prefer rollback-first recovery after migration failure.
  reason: approved strategy with recent validation
```

## 7. Provider lifecycle

### `initialize(session_id, **kwargs)`

Capture profile, session, agent, workspace, platform, execution kind, and trusted
`hermes_home`. Initialize paths lazily. No network calls are allowed.

### `system_prompt_block()`

Return a small static capability block only. It should describe Mnemosyne as a
local, explainable Memory Tower and remind the model that L4 candidates are not
approved facts. It must not dump full memory content.

### `prefetch(query, session_id="")`

Call the Injector. It is read-only. It uses the active profile and current
execution kind to choose budgets and layers.

### `sync_turn(user_content, assistant_content, messages=None)`

Call the Observer to create ephemeral observations. The default implementation
does not write L1/L2/L3. It may queue bounded L4 candidate observations only if
configured and only through the L4 candidate API.

### `on_session_end(messages)`

Call the Refiner. It may write L4 candidates. It must not write L1/L2 facts or
promote L4. It must never block cleanup.

### `on_memory_write(action, target, content, metadata=None)`

Observe successful built-in memory writes and optionally create L4 source refs.
This hook must not mirror writes back into L1 or L2.

### Tools

The first implementation may expose no model-callable tools. If a diagnostic
tool is added later, it must be read-only unless separately designed and
approved.

## 8. Observer, Injector, and Refiner semantics

The semantic boundary is strict:

```text
Observer output != memory
Refiner candidate != approved memory
Session-end candidate != persisted Core
```

Observer outputs are ephemeral unless explicitly written as L4 candidates.
Refiner outputs are candidate or conflict reports. Injector outputs are context
only and cannot mutate source records.

Conflict reports may include:

- layer conflicts;
- old/new candidate disagreement;
- Core versus episodic mismatch;
- SkillWiki health/degraded warnings;
- stale or archive-recommended L4 records.

Reports are advisory unless a future approved flow connects them to an explicit
write or review command.

## 9. Persistence and Governance boundaries

Persistence authority is split by layer:

```text
L1: Memory Governance and built-in memory tool.
L2: Holographic fact store APIs and their existing update semantics.
L3: SkillWiki and Skills Hub lifecycle/provenance APIs.
L4: Mnemosyne controlled candidate API only.
```

Mnemosyne cannot bypass these authorities. In particular:

- L1 long-term Core writes must pass existing Memory Governance.
- Mnemosyne performs no L2 writes in this phase; future L2 writes need a
  separately approved fact-store governance path.
- L3 lifecycle remains explicit SkillWiki state transition only.
- L4 writes create candidates, not approved facts.
- L4-to-L1/L2 promotion is out of scope and must be explicitly approved later.

Malformed L4 JSONL records are ignored for reads and preserved during writes.
L4 writes use a profile-scoped lock, same-directory temporary file, flush, fsync,
and atomic replacement.

## 10. Profile, Agent, Cron, and Subagent isolation

All state is scoped by active profile and trusted `HERMES_HOME`. Records and
injection decisions include:

- `profile_id`
- `session_id`
- `agent_id`
- `execution_kind`
- `parent_session_id` when applicable

Cron and Subagent behavior:

- Cron uses smaller budgets and never inherits interactive temporary context.
- Subagents receive no full sensitive L1 content by default.
- Subagents only receive explicitly allowed context and copied callbacks.
- Subagent session-end candidates must not be written as primary session Core.
- Provider mutable state must be per instance and not shared globally.

## 11. Failure model and fail-open behavior

Layer failures are isolated:

- L1 read failure drops L1.
- L2 store/retriever failure drops L2.
- L3 SkillWiki unavailable drops L3.
- L4 malformed storage drops malformed records and keeps valid records.

Provider-level failures are logged and return empty context so the main Agent
continues. A layer timeout only drops that layer. No Mnemosyne read path should
raise into the Agent main loop.

Persistence failures for L4 candidates are reported as diagnostics and fail
open. They do not block cleanup, response delivery, or other memory providers.

## 12. Injection budgeting and ranking

`prefetch()` uses a total character budget and per-layer caps. Budgets are
configurable but bounded.

Default ranking:

1. L1 Core receives a fixed small budget or summary because it is high-priority
   but sensitive.
2. L2 Episodic receives the largest dynamic budget for query-relevant facts.
3. L3 Skill/API is included only when query intent is skill, tool, API, install,
   provenance, or workflow related.
4. L4 Reflection receives a low budget for strategies and experiences.

Do not split budget evenly across layers. Empty query behavior is conservative:
L1 may contribute a minimal capability/profile summary, while L2/L3/L4 require
query or intent signals.

Deduplication:

- Normalize content and provenance IDs.
- If the same item appears across layers, prefer the highest-authority layer but
  merge lower-layer reason/provenance into the item metadata.
- Core conflicts are surfaced as conflict reports, not silently hidden.

Sorting combines normalized layer priority, layer score, trust, freshness,
confidence, and query overlap. Scores are always clamped to `[0.0, 1.0]`.

## 13. L4 decay and archive suggestions

Decay is computed dynamically at read time. The default review threshold is 90
days.

Rules:

- `decay_score` may decrease with age and lack of validation.
- `archive_recommended=True` may be returned for stale L4 records.
- Querying or prefetching must not change `status`, `archived_at`, or
  `last_validated_at`.
- Automatic archive is out of scope.

The system may later add explicit CLI commands to approve, reject, archive, or
validate L4 records. That is not part of the first implementation unless a
separate plan is approved.

## 14. Bridge abstraction

Define an interface only:

```python
class MnemosyneBridge(Protocol):
    def fetch(self, query: str, *, layer: str) -> list[MnemosyneItem]: ...
    def publish(self, items: list[MnemosyneItem]) -> None: ...
```

The default provider registers no remote bridge implementation. This phase must
not introduce dynamic URLs, HTTP clients, credential reads, background sync
threads, or remote publish/fetch calls.

Tests should be able to monkeypatch common socket/HTTP paths and assert zero
network access during provider initialization, prefetch, sync, and session-end.

## 15. Security considerations

- Memory paths are resolved from trusted profile directories only.
- Query text is never treated as a path, SQL fragment, config key, or lifecycle
  command.
- Injected memory remains fenced by existing `MemoryManager` context wrapping.
- Sensitive L1 content is minimized for Cron/Subagent contexts.
- L3 advisory and health output cannot become actions.
- L4 candidate content is bounded and JSON-serializable.
- Missing, corrupt, or unavailable layer data never blocks Hermes startup.
- Reasons and provenance should be informative but not leak credentials or full
  environment variables.

## 16. TDD acceptance matrix

Focused tests:

- Provider discovery loads `mnemosyne` through `plugins.memory.load_memory_provider`.
- Initialization uses active `HERMES_HOME` and creates no network activity.
- `system_prompt_block()` is static and does not dump memory contents.
- `prefetch()` returns bounded `MnemosyneItem`-derived context with required
  `reason` fields.
- L1 missing files fail open.
- L1 path resolution ignores query-provided path-like strings.
- L2 prefetch/reconstruct does not update retrieval count, trust, or
  `last_retrieved_at`.
- L2 failure does not suppress L1/L3/L4.
- L3 SkillWiki unavailable fails open.
- L3 includes only visible, non-disabled skills and never changes lifecycle.
- L4 candidate writes are profile-scoped, locked, atomic, bounded, and
  malformed-line preserving.
- `on_session_end()` can write L4 candidates but cannot write L1/L2.
- `sync_turn()` observations are not automatically Core memory.
- Cron and Subagent budgets differ from interactive budgets.
- Subagent context omits full sensitive L1 by default.
- Duplicate cross-layer items merge provenance and reasons.
- L4 age over 90 days returns `archive_recommended=True` without changing
  status or `archived_at`.
- Bridge protocol exists but default provider performs zero network access.

Relevant regression tests:

```bash
pytest tests/agent/test_memory_provider.py -q
pytest tests/plugins/memory/test_mnemosyne.py -q
pytest tests/plugins/memory/test_holographic_retrieval.py \
       tests/plugins/memory/test_holographic_store.py -q
pytest tests/tools/test_memory_governance.py \
       tests/tools/test_skillwiki.py -q
git diff --check
python -m compileall -q plugins/memory/mnemosyne agent tools
```

The exact command paths should use `./.venv/bin/python -m pytest` when running
inside the local MyHermes development environment.

## 17. Rollout and rollback

Rollout:

1. Add spec and implementation plan first.
2. Implement under a bundled provider gated by `memory.provider: mnemosyne`.
3. Keep the existing provider selection model unchanged.
4. Do not enable Mnemosyne by default for profiles that did not opt in.
5. Run focused memory, SkillWiki, and provider tests.

Rollback:

- Remove or change `memory.provider` in profile config to disable Mnemosyne.
- L1 rollback remains Memory Governance rollback.
- L2 data remains in existing holographic storage.
- L3 data remains in SkillWiki.
- L4 candidate records are local JSONL and can be ignored by disabling the
  provider. No migration should be required to start Hermes without Mnemosyne.
