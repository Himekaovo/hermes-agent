# Built-in Memory Governance

MyHermes protects profile-scoped `MEMORY.md` and `USER.md` writes with a local,
deterministic governance layer.

Accepted edits create a pre-write snapshot in `memories/l2/versions/`. Restore
is always manual and requires explicit confirmation:

```bash
hermes memory versions list
hermes memory versions rollback <version_id> --target memory --yes
```

Rollback validates the snapshot sidecar, target, and filename-safe version id;
the current file is snapshotted before restoration and the restore uses an
atomic replacement. Snapshot and mutation checks run under the same target
file lock, including journey edits and deletes.

Entries marked with `<!-- SLOW_UPDATE -->` or a complete named protected span
cannot be replaced or removed through the memory tool or journey editor.
Named spans may cross `§`-delimited entries; malformed spans fail closed for
the affected entries. Journey mutations re-read and compare the selected
entry while holding the target lock, so a stale index cannot silently modify a
different entry.
Near-duplicates, contradictory preference toggles, and malformed delimiter
content are rejected before mutation. Existing memory approval and atomic file
writes remain active.

Repeated failed write patterns and strategy outcomes are local JSONL records:

```bash
hermes memory step record "replace a preference with a contradictory rule"
hermes memory step list
hermes memory meta log "merge overlapping entries" --result success
hermes memory meta list
```

Step Buffer entries can block a repeated pattern after the second record. Meta
Skill records are advisory only; they never rewrite, merge, delete, schedule,
or roll back memory automatically.
