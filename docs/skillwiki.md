# SkillWiki Provenance

SkillWiki adds local provenance and lifecycle metadata to the existing Skills
Hub. It does not replace the Hub installer. GitHub fetching, scanning,
quarantine, path validation, lock-file updates, and local modification checks
remain in the existing installation path.

## Database

The database is stored at:

```text
<active Hub state directory>/provenance.db
```

Each record stores the source repository and path, ref, commit, source URL,
content fingerprint, import time, local path, version, and local modification
count. The record starts in `raw` when an existing Hub installation succeeds.

If the database is unavailable or corrupt, Skill Hub installation continues and
the CLI reports the provenance layer as unavailable. SkillWiki never prevents
Hermes from starting.

## Lifecycle

Lifecycle changes are explicit. The normal review path is:

```text
raw -> candidate -> draft -> verified -> release
```

The additional operator states are `degraded`, `deprecated`, and `archived`.
Invalid jumps are rejected. Evaluation and Hook observations are advisory and
cannot change lifecycle state automatically.

## Relations

The supported directed relation types are:

- `inspired_by`
- `depends_on`
- `references`

Relations are metadata only. In particular, `depends_on` does not resolve or
install third-party dependencies.

## Commands

```text
hermes skills wiki import <identifier>
hermes skills wiki list [--status STATUS] [--source SOURCE] [--json]
hermes skills wiki show <skill-id> [--json]
hermes skills wiki relation add <from> <to> --type TYPE
hermes skills wiki relation remove <from> <to> --type TYPE
hermes skills wiki relation list [<skill-id>] [--json]
hermes skills wiki status <skill-id> <new-status> [--reason TEXT]
hermes skills wiki check [<skill-id>] [--json]
```

`wiki import` delegates to the normal Hub install flow, including its security
scan and confirmation behavior. It never overwrites a locally modified skill
through a separate path.

`wiki check` is read-only. It reports missing local paths, content drift,
unresolved relation targets, and database availability without rewriting skill
files, changing lifecycle state, or installing anything.
