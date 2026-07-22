# Skill Prompt Compression Design

## Goal

Reduce the per-session skills index in the system prompt while preserving routing information and keeping full skill instructions available through `skill_view()`.

## Scope

- Add an optional compact skill summary with three fields: `triggers`, `steps`, and `warnings`.
- Render only the compact summary in the system-prompt skills index.
- Keep complete `SKILL.md` and support files unchanged and loadable on demand.
- Limit the rendered compact summary to 200 characters per skill.
- Use a conservative description fallback for legacy skills without the new metadata.
- Bump the skills snapshot version so old expanded snapshots are invalidated.
- Add authoring guidance and validation for new/edit skill content.

## Non-goals

- No model-based summarization or automatic rewriting of existing skills.
- No scheduled or periodic compression job.
- No deletion, truncation, or mutation of `SKILL.md` bodies.
- No change to `skill_view()` full-content semantics.

## Data Shape

Skills may declare the compact summary in frontmatter:

```yaml
metadata:
  hermes:
    compact:
      triggers: "when the task needs..."
      steps: "load; run; verify"
      warnings: "requires API key"
```

The parser also accepts top-level `triggers`, `steps`, and `warnings` for compatibility with hand-authored skills. The rendered line is deterministic:

```text
T: ... | S: ... | W: ...
```

Missing fields fall back to the legacy description and `skill_view(name)` guidance rather than inventing semantic content. The final rendered line is normalized and capped at 200 characters.

## Prompt and Cache Behavior

`build_skills_system_prompt()` stores the compact summary in its in-process and disk snapshot entries. The snapshot schema version is incremented, so any previous snapshot is ignored and rebuilt. Existing mtime/size manifest invalidation remains in place. Full skill bodies remain outside the system prompt and are returned by `skill_view()` only when requested.

## Validation and Compatibility

Skill authoring guidance requires the compact fields for new skills, while legacy skills remain accepted. Validation rejects an explicitly supplied compact summary whose normalized total exceeds 200 characters. Existing description validation and all other frontmatter rules remain unchanged.

## Testing

- Parse nested and top-level compact metadata.
- Normalize and cap summaries without corrupting field boundaries.
- Reject oversized new/edit summaries.
- Render compact summaries in the prompt and omit long legacy descriptions beyond the fallback cap.
- Invalidate old prompt snapshots after the version bump.
- Preserve full `skill_view()` content.
- Verify prompt-size reduction with a long-description fixture.
