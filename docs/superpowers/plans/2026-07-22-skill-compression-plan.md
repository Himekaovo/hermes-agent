# Skill Prompt Compression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the skills index compact with deterministic T/S/W summaries while retaining full skill instructions for explicit `skill_view()` loads.

**Architecture:** Parse compact metadata through shared skill utilities, store the rendered summary in the existing prompt snapshot, and render it in place of verbose descriptions. Skill management validates the same 200-character budget for new and edited skills.

**Tech Stack:** Python stdlib plus the repository's existing YAML parser and pytest suite.

## Global Constraints

- Never mutate or delete existing `SKILL.md` bodies as part of compression.
- Do not call a model or add a background compression scheduler.
- Keep `skill_view()` full-content behavior unchanged.
- Keep legacy skills loadable with a bounded description fallback.
- Invalidate old prompt snapshots by schema version.

---

### Task 1: Add shared compact-summary parsing

**Files:**
- Modify: `agent/skill_utils.py`
- Test: `tests/agent/test_skill_utils.py`

**Interfaces:**
- Produce `extract_compact_skill_summary(frontmatter, description) -> str`.
- Produce `validate_compact_skill_summary(frontmatter) -> Optional[str]`.

- [ ] Add tests for nested `metadata.hermes.compact`, top-level aliases, missing fields, normalization, and the 200-character limit.
- [ ] Run focused tests and confirm the new tests fail.
- [ ] Implement deterministic parsing with a bounded legacy fallback.
- [ ] Run the focused utility tests.
- [ ] Commit `feat: add compact skill summary parsing`.

### Task 2: Render summaries and invalidate snapshots

**Files:**
- Modify: `agent/prompt_builder.py`
- Test: `tests/agent/test_prompt_builder.py`
- Test: `tests/hermes_cli/test_prompt_size.py`

**Interfaces:**
- Snapshot entries carry `compact_summary`.
- `build_skills_system_prompt()` renders the summary after the skill name.

- [ ] Add prompt tests for T/S/W output, legacy fallback capping, and snapshot version invalidation.
- [ ] Run focused tests and confirm failure.
- [ ] Bump `_SKILLS_SNAPSHOT_VERSION` and use the shared summary extractor in cold and snapshot paths.
- [ ] Run prompt-builder and prompt-size tests.
- [ ] Commit `feat: compact skills system prompt index`.

### Task 3: Enforce authoring budget and document the format

**Files:**
- Modify: `tools/skill_manager_tool.py`
- Modify: `agent/learn_prompt.py`
- Test: `tests/tools/test_skill_manager_tool.py` or existing skill manager tests
- Test: `tests/agent/test_learn_prompt.py`

**Interfaces:**
- New and edit validation rejects explicit compact summaries over 200 characters.
- `/learn` instructs the agent to author the three compact fields.

- [ ] Add tests for create/edit rejection and learn prompt guidance.
- [ ] Run focused tests and confirm failure.
- [ ] Call shared validation from the existing frontmatter validator and extend authoring standards.
- [ ] Run skill manager and learn-prompt tests.
- [ ] Commit `feat: enforce compact skill authoring format`.

### Task 4: Full verification and publication

**Files:**
- Review: all files changed by Tasks 1-3

- [ ] Run all focused Prompt, Skill, Skill Hub, and Skill Manager tests.
- [ ] Run compile checks and `git diff --check`.
- [ ] Verify `skill_view()` still returns the complete body for a fixture with compact metadata.
- [ ] Review the final diff for accidental body mutation or automatic scheduling.
- [ ] Commit any final test-only adjustments.
- [ ] Push `feature/my-hermes-agent` and report the remote commit and links.
