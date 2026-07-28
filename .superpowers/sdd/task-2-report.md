# Task 2 Report: Secure L4 Candidate Store

## Scope

Implemented only the Mnemosyne L4 candidate-record persistence path:

- Added `plugins/memory/mnemosyne/l4_store.py`.
- Added L4 persistence tests in `tests/plugins/memory/test_mnemosyne.py`.
- Reused the Task 1 `MnemosyneConfig` L4 size limits and execution-budget behavior unchanged.

No injector, adapter, provider lifecycle, bridge, documentation, L1/L2 mutation, network access, background work, model calls, or default activation was added.

## TDD Evidence

The specified RED command was run after the new tests were added:

```text
./.venv/bin/python -m pytest \
  tests/plugins/memory/test_mnemosyne.py::test_l4_write_preserves_malformed_lines_and_is_idempotent \
  tests/plugins/memory/test_mnemosyne.py::test_l4_decay_is_read_time_only_and_legacy_decay_score_is_ignored \
  -q
```

It failed as expected with `ModuleNotFoundError: No module named 'plugins.memory.mnemosyne.l4_store'` for both tests.

## Behavior Delivered

- Candidate IDs are deterministic SHA-256-derived `l4_` identifiers over the specified isolation and candidate fields, including `parent_session_id`.
- Candidate payloads persist `governance_state` and exclude derived `decay_score`, `age_days`, and `archive_recommended` fields.
- JSONL writes preserve malformed existing lines, reject duplicate IDs, enforce record and file character limits, and rewrite atomically with file and directory fsync where supported.
- Reads report malformed/invalid entries diagnostically, enforce the profile boundary, omit rejected/archived candidates, and derive decay state at read time.
- Trusted-path checks reject both root escapes and symlink traversal. The latter is necessary because the supplied symlink test targets a directory still under the temporary root; containment after `resolve()` alone would otherwise accept it.

## Verification

```text
./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py -q
8 passed in 0.12s
```

```text
./.venv/bin/python -m py_compile \
  plugins/memory/mnemosyne/l4_store.py \
  plugins/memory/mnemosyne/contracts.py
```

`git diff --check` completed without output.

## Working Tree Isolation

Pre-existing changes in `gateway/channel_directory.py` and `tests/gateway/test_background_command.py` were not read, modified, staged, reverted, or committed.

## Review Fixes

- `ensure_available()` now validates the final `l4.jsonl` path as well as its parent. L4 reads repeat validation and open the file with `O_NOFOLLOW` where available; writes validate again inside the lock and immediately before creating/replacing the atomic rewrite. A final-file symlink escape now disables/rejects the store without reading or writing its target.
- Atomic rewrites add a single newline separator when preserved existing bytes do not end in a line ending, so an unterminated malformed line remains unchanged and cannot absorb the next JSONL record.
- `L4CandidateRecord.from_mapping()` removes the complete derived `read_state` object before persistence.
- `parent_session_id` is constrained to `str | None`, and its canonical ID encoding distinguishes `None` from `""`.
- Removed the unused `timedelta` test import.

## Review Regression TDD Evidence

RED: the newly added regressions were run before production changes:

```text
./.venv/bin/python -m pytest \
  tests/plugins/memory/test_mnemosyne.py::test_l4_file_symlink_escape_is_rejected_without_touching_target \
  tests/plugins/memory/test_mnemosyne.py::test_l4_write_separates_unterminated_malformed_line_and_remains_idempotent \
  tests/plugins/memory/test_mnemosyne.py::test_l4_read_state_is_not_persisted_when_rewriting_read_output \
  tests/plugins/memory/test_mnemosyne.py::test_l4_mapping_read_state_is_not_persisted \
  tests/plugins/memory/test_mnemosyne.py::test_l4_parent_session_id_type_and_none_id_are_distinct_from_empty -q

5 failed in 0.10s
```

The failures demonstrated the final-file symlink was accepted, an unterminated malformed line caused duplicate writes, `read_state` was persisted, `None` and `""` shared an ID, and integer parent IDs were accepted.

GREEN: after the review fixes:

```text
./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py -q

13 passed in 0.08s
```

```text
./.venv/bin/python -m py_compile plugins/memory/mnemosyne/l4_store.py
git diff --check
```

Both commands completed successfully with no output.

## Remaining Re-review Fix

`L4CandidateRecord` remains a frozen dataclass, but its `payload` dict is mutable.
`write_candidate()` now creates a fresh persistence copy, removes all read-time-derived
fields (`read_state`, `decay_score`, `age_days`, and `archive_recommended`), preserves
the prior `parent_session_id` default/type rule, and overwrites the copied `record_id`
with `record.record_id`. This keeps duplicate detection and the record ID stable even
when a caller mutates `record.payload` after construction.

## Remaining Re-review TDD Evidence

RED: the focused regression was added and run before the production fix:

```text
.venv/bin/pytest -q tests/plugins/memory/test_mnemosyne.py::test_l4_write_resanitizes_mutated_candidate_payload

F                                                                        [100%]
...
E       AssertionError: assert 'read_state' not in {'age_days': 7, 'agent_id': 'agent-main', 'archive_recommended': False, 'archived_at': None, ...}
1 failed in 0.09s
```

GREEN: after the write-boundary re-sanitization:

```text
.venv/bin/pytest -q tests/plugins/memory/test_mnemosyne.py::test_l4_write_resanitizes_mutated_candidate_payload

.                                                                        [100%]
1 passed in 0.07s
```

```text
.venv/bin/pytest -q tests/plugins/memory/test_mnemosyne.py

..............                                                           [100%]
14 passed in 0.08s
```

`git diff --check` completed without output.

## Final Review Fixes

- `L4CandidateRecord.from_mapping()` retains a deep canonical construction snapshot.
  `write_candidate()` now persists that snapshot rather than the caller-mutable
  `payload`, so post-construction mutations cannot change identity or isolation
  fields while retaining the original `record_id`.
- `write_candidate()` rejects a record whose current `payload["profile_id"]`
  differs from `L4Store.profile_id` before it can cross the persistence boundary.
- `read_records()` removes every legacy top-level derived field (`decay_score`,
  `age_days`, and `archive_recommended`) before adding the computed `read_state`.

## Final Review GREEN Evidence

```text
./.venv/bin/python -m pytest \
  tests/plugins/memory/test_mnemosyne.py::test_l4_write_preserves_constructed_identity_after_payload_mutation \
  tests/plugins/memory/test_mnemosyne.py::test_l4_write_rejects_mutated_profile_id \
  tests/plugins/memory/test_mnemosyne.py::test_l4_read_strips_all_legacy_derived_fields \
  -q

3 passed in 0.07s
```

```text
./.venv/bin/python -m pytest tests/plugins/memory/test_mnemosyne.py -q

17 passed in 0.07s
```

`./.venv/bin/python -m py_compile plugins/memory/mnemosyne/l4_store.py` and
`git diff --check` completed successfully.
